"""Tests for DUO Items 1-6. No real Ollama needed: a fake Ollama HTTP
server (and a fake client for the GUI tests) stands in for the real one.

Run from the project root:  python3 -m unittest discover -s tests -v
"""
import copy
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dual_chat import (  # noqa: E402
    AGREEMENT_PHRASES, AWARE_TEMPLATE, COMPARE_PREFIX, MAX_ATTACHMENTS,
    MAX_ATTACHMENT_BYTES, TASK_CONTINUE_TEMPLATE, TASK_FOLLOW_UPS,
    TASK_REVIEW_TEMPLATE, AttachmentError, Cancelled, ChatSide,
    Conversation, HISTORY_BUDGET_CHARS, NoModelSelected, OllamaClient,
    OllamaError, RelaySession, SUMMARY_KEEP_LAST, SUMMARY_LABEL,
    SUMMARIZE_PROMPT, SerialRunner, TaskSession, attachment_tag,
    build_attached_message, compute_turn_stats, format_ram,
    format_stats_line, load_attachment, load_pairs, pairs_path,
    parse_ollama_list_output, parse_ps_payload, parse_stop_phrases,
    parse_tags_payload, save_pair,
)


# ---------------------------------------------------------------------------
# Fake Ollama server
# ---------------------------------------------------------------------------

class FakeOllamaHandler(BaseHTTPRequestHandler):
    models = ["fake-a:latest", "fake-b:latest"]
    # model -> list of streamed chunks
    replies = {
        "fake-a:latest": ["Hello ", "from A."],
        "fake-b:latest": ["Hello ", "from B."],
    }
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._send_json({"models": [{"name": n} for n in self.models]})
        elif self.path == "/api/ps":
            self._send_json({"models": [
                {"name": "fake-a:latest", "size": 1900000000},
                {"name": "fake-b:latest", "size": 1500000000},
            ]})
        else:
            self._send_json({"error": "not found"}, status=404)

    def do_POST(self):
        if self.path != "/api/chat":
            self._send_json({"error": "not found"}, status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        model = payload.get("model")
        if model not in self.replies:
            self._send_json(
                {"error": "model '%s' not found" % model}, status=404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        try:
            for chunk in self.replies[model]:
                line = json.dumps(
                    {"message": {"role": "assistant", "content": chunk},
                     "done": False}).encode("utf-8") + b"\n"
                self.wfile.write(line)
                self.wfile.flush()
                time.sleep(0.01)
            self.wfile.write(
                json.dumps({"message": {"role": "assistant", "content": ""},
                            "done": True, "done_reason": "stop",
                            "eval_count": 20,
                            "eval_duration": 1000000000,
                            "prompt_eval_duration": 200000000,
                            "total_duration": 1500000000}
                           ).encode("utf-8") + b"\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # Client cancelled mid-stream; expected in cancel tests.


def start_fake_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllamaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

class ParseTagsTests(unittest.TestCase):
    def test_names_sorted(self):
        payload = {"models": [{"name": "b:2b"}, {"name": "a:2b"}]}
        self.assertEqual(parse_tags_payload(payload), ["a:2b", "b:2b"])

    def test_empty_and_missing(self):
        self.assertEqual(parse_tags_payload({}), [])
        self.assertEqual(parse_tags_payload({"models": []}), [])
        self.assertEqual(parse_tags_payload({"models": [{"size": 1}]}), [])


class ParseOllamaListTests(unittest.TestCase):
    SAMPLE = """NAME                    ID              SIZE      MODIFIED
qwen3.5:2b              abc123        1.9 GB    2 hours ago
minicpm5:2b             def456        1.5 GB    3 days ago

"""

    def test_rows_parsed_header_skipped(self):
        self.assertEqual(parse_ollama_list_output(self.SAMPLE),
                         ["minicpm5:2b", "qwen3.5:2b"])

    def test_header_only(self):
        self.assertEqual(
            parse_ollama_list_output("NAME  ID  SIZE  MODIFIED\n"), [])


# ---------------------------------------------------------------------------
# Conversation / routing
# ---------------------------------------------------------------------------

class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.convo = Conversation()
        self.convo.sides["A"].model = "fake-a:latest"
        self.convo.sides["B"].model = "fake-b:latest"

    def test_histories_are_independent(self):
        self.convo.sides["A"].add_user("hello A")
        self.convo.sides["A"].add_assistant("hi")
        self.assertEqual(len(self.convo.sides["A"].history), 2)
        self.assertEqual(self.convo.sides["B"].history, [])

    def test_clear_only_clears_one_side(self):
        self.convo.sides["A"].add_user("x")
        self.convo.sides["B"].add_user("y")
        self.convo.sides["A"].clear()
        self.assertEqual(self.convo.sides["A"].history, [])
        self.assertEqual(len(self.convo.sides["B"].history), 1)

    def test_targets_a_only(self):
        self.assertEqual([s.name for s in self.convo.targets_for("A")], ["A"])

    def test_targets_b_only(self):
        self.assertEqual([s.name for s in self.convo.targets_for("B")], ["B"])

    def test_targets_both_orders_a_first(self):
        self.assertEqual([s.name for s in self.convo.targets_for("Both")],
                         ["A", "B"])

    def test_no_model_raises_for_that_side(self):
        self.convo.sides["B"].model = None
        with self.assertRaises(NoModelSelected) as ctx:
            self.convo.targets_for("B")
        self.assertEqual(ctx.exception.side, "Model B")
        with self.assertRaises(NoModelSelected):
            self.convo.targets_for("Both")

    def test_chat_side_defaults(self):
        side = ChatSide("A")
        self.assertIsNone(side.model)
        self.assertEqual(side.history, [])


# ---------------------------------------------------------------------------
# Relay session: framing, stop checks, turn cap (pure logic, no Qt)
# ---------------------------------------------------------------------------

class RelaySessionTests(unittest.TestCase):
    def test_defaults(self):
        session = RelaySession("A")
        self.assertEqual(session.turn_cap, 10)
        self.assertEqual(session.mode, "blind")
        self.assertEqual(session.stop_phrases, [])
        self.assertFalse(session.stop_on_agreement)
        self.assertTrue(session.active)
        self.assertEqual(session.turns_done, 0)

    def test_turn_cap_clamped(self):
        self.assertEqual(RelaySession("A", turn_cap=0).turn_cap, 1)
        self.assertEqual(RelaySession("A", turn_cap=99).turn_cap, 50)
        self.assertEqual(RelaySession("A", turn_cap=7).turn_cap, 7)

    def test_other_side(self):
        self.assertEqual(RelaySession.other("A"), "B")
        self.assertEqual(RelaySession.other("B"), "A")

    def test_blind_frame_returns_bare_text(self):
        session = RelaySession("A", mode="blind")
        self.assertEqual(session.frame("hello there", "A"), "hello there")

    def test_aware_frame_names_the_other_model(self):
        session = RelaySession("A", mode="aware")
        framed = session.frame("hello there", "A")
        self.assertEqual(
            framed, AWARE_TEMPLATE % ("A", "hello there"))
        self.assertIn("Model A", framed)
        self.assertIn("hello there", framed)

    def test_parse_stop_phrases(self):
        self.assertEqual(parse_stop_phrases("a, b ,c"), ["a", "b", "c"])
        self.assertEqual(parse_stop_phrases("  one  , , two,"), ["one", "two"])
        self.assertEqual(parse_stop_phrases(""), [])
        self.assertEqual(parse_stop_phrases(None), [])

    def test_stop_phrase_case_insensitive_substring(self):
        session = RelaySession("A", stop_phrases=["PineApple"])
        self.assertEqual(session.find_stop_phrase(
            "I like pineapple pizza."), "PineApple")
        self.assertIsNone(session.find_stop_phrase("I like apples."))

    def test_agreement_only_when_enabled(self):
        session = RelaySession("A", stop_on_agreement=False)
        self.assertIsNone(session.find_agreement("Sounds good!"))
        session = RelaySession("A", stop_on_agreement=True)
        self.assertEqual(session.find_agreement("Sounds good!"),
                         "sounds good")
        self.assertEqual(session.find_agreement("I AGREE with you."),
                         "i agree")
        self.assertIsNone(session.find_agreement("Hmm, let me think."))

    def test_agreement_phrase_list_is_fixed(self):
        self.assertEqual(tuple(sorted(AGREEMENT_PHRASES)),
                         ("agreed", "i agree", "sounds good", "that works"))

    def test_check_stop_cap_reached(self):
        session = RelaySession("A", turn_cap=3)
        session.turns_done = 2
        self.assertIsNone(session.check_stop("nothing special"))
        session.turns_done = 3
        self.assertEqual(session.check_stop("nothing special"),
                         "turn cap reached")

    def test_check_stop_phrase_beats_cap(self):
        session = RelaySession("A", turn_cap=1, stop_phrases=["banana"])
        session.turns_done = 1
        self.assertEqual(session.check_stop("I said banana."),
                         "phrase 'banana' found")

    def test_check_stop_agreement(self):
        session = RelaySession("A", turn_cap=10, stop_on_agreement=True)
        session.turns_done = 1
        self.assertEqual(session.check_stop("Agreed, moving on."),
                         "agreement reached ('agreed')")

    def test_status_running(self):
        session = RelaySession("A", turn_cap=10)
        self.assertEqual(session.status_running(),
                         "Relay on: turn 1/10, you to A")
        session.turns_done = 2
        session.current_side = "B"
        session.hop_from = "A"
        self.assertEqual(session.status_running(),
                         "Relay on: turn 3/10, A to B")


class TaskSessionTests(unittest.TestCase):
    def test_defaults(self):
        session = TaskSession("A", "review")
        self.assertEqual(session.worker, "A")
        self.assertEqual(session.follow_up, "review")
        self.assertEqual(session.phase, "worker")
        self.assertTrue(session.active)
        self.assertEqual(session.other, "B")

    def test_other_side(self):
        self.assertEqual(TaskSession("B", "nothing").other, "A")

    def test_frame_review_exact(self):
        session = TaskSession("A", "review")
        self.assertEqual(
            session.frame("The draft is done."),
            "Here is the result of a task. Review it and say what is "
            "good, what is wrong, and what you would change:\n\n"
            "The draft is done.")
        # The template constant is the source of truth.
        self.assertEqual(session.frame("X"), TASK_REVIEW_TEMPLATE % "X")

    def test_frame_continue_exact(self):
        session = TaskSession("B", "continue")
        self.assertEqual(
            session.frame("Part one done."),
            "Continue this work from where it left off:\n\n"
            "Part one done.")
        self.assertEqual(session.frame("X"), TASK_CONTINUE_TEMPLATE % "X")

    def test_frame_nothing_is_none(self):
        self.assertIsNone(TaskSession("A", "nothing").frame("anything"))

    def test_follow_up_names(self):
        self.assertEqual(TASK_FOLLOW_UPS, ("review", "continue", "nothing"))

    def test_handoff_note(self):
        self.assertEqual(
            TaskSession("A", "review").handoff_note(),
            "Task result from Model A, for review.")
        self.assertEqual(
            TaskSession("B", "continue").handoff_note(),
            "Task result from Model B, to continue.")


# ---------------------------------------------------------------------------
# Serial runner: one at a time, in order, cancellable
# ---------------------------------------------------------------------------

class SerialRunnerTests(unittest.TestCase):
    def test_jobs_run_one_at_a_time_in_order(self):
        events = []
        started = {"A": threading.Event(), "B": threading.Event()}
        release_a = threading.Event()
        done = threading.Event()

        def make_fn(name):
            def fn(emit, cancel_event):
                events.append("start-" + name)
                started[name].set()
                if name == "A":
                    release_a.wait(timeout=5)
                events.append("end-" + name)
                return name
            return fn

        runner = SerialRunner(on_done=lambda j, t: (
            done.set() if j["id"] == "B" else None))
        runner.submit("A", make_fn("A"))
        runner.submit("B", make_fn("B"))
        self.assertTrue(started["A"].wait(timeout=5))
        time.sleep(0.2)
        # A is still running: B must not have started yet.
        self.assertFalse(started["B"].is_set())
        self.assertEqual(events, ["start-A"])
        release_a.set()
        self.assertTrue(done.wait(timeout=5))
        self.assertEqual(events, ["start-A", "end-A", "start-B", "end-B"])

    def test_cancel_queued_job(self):
        release = threading.Event()
        cancelled = []
        done = threading.Event()

        def fn_a(emit, cancel_event):
            release.wait(timeout=5)
            return "A"

        def fn_b(emit, cancel_event):
            raise AssertionError("cancelled job must never run")

        runner = SerialRunner(
            on_cancelled=lambda j: cancelled.append(j["id"]),
            on_idle=lambda: done.set())
        runner.submit("A", fn_a)
        runner.submit("B", fn_b)
        time.sleep(0.3)
        runner.cancel("B")
        release.set()
        self.assertTrue(done.wait(timeout=5))
        self.assertEqual(cancelled, ["B"])

    def test_cancel_running_job(self):
        cancelled = []
        done = threading.Event()

        def fn(emit, cancel_event):
            while not cancel_event.is_set():
                time.sleep(0.01)
            raise Cancelled()

        runner = SerialRunner(
            on_cancelled=lambda j: cancelled.append(j["id"]),
            on_idle=lambda: done.set())
        runner.submit("A", fn)
        time.sleep(0.3)
        runner.cancel("A")
        self.assertTrue(done.wait(timeout=5))
        self.assertEqual(cancelled, ["A"])

    def test_cancel_does_not_poison_next_job_with_same_id(self):
        # Regression: cancelling a running job used to leave its id in
        # the cancelled set, so the NEXT job with that id (a new send
        # to the same side) was instantly cancelled too.
        cancelled = []
        finished = []
        idle = threading.Event()

        def fn(emit, cancel_event):
            while not cancel_event.is_set():
                time.sleep(0.01)
            raise Cancelled()

        runner = SerialRunner(
            on_done=lambda j, t: finished.append(j["id"]),
            on_cancelled=lambda j: cancelled.append(j["id"]),
            on_idle=lambda: idle.set())
        runner.submit("A", fn)
        time.sleep(0.3)
        runner.cancel("A")
        self.assertTrue(idle.wait(timeout=5))
        self.assertEqual(cancelled, ["A"])
        idle.clear()
        runner.submit("A", lambda emit, ev: "fresh")
        self.assertTrue(idle.wait(timeout=5))
        self.assertEqual(finished, ["A"])
        self.assertEqual(cancelled, ["A"])

    def test_error_reported_plainly(self):
        errors = []
        done = threading.Event()

        def fn(emit, cancel_event):
            raise OllamaError("Ollama isn't running.")

        runner = SerialRunner(
            on_error=lambda j, msg: errors.append(msg),
            on_idle=lambda: done.set())
        runner.submit("A", fn)
        self.assertTrue(done.wait(timeout=5))
        self.assertEqual(errors, ["Ollama isn't running."])


# ---------------------------------------------------------------------------
# Ollama client against the fake server
# ---------------------------------------------------------------------------

class OllamaClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.url = start_fake_server()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_list_models(self):
        client = OllamaClient(self.url)
        self.assertEqual(client.list_models(), ["fake-a:latest", "fake-b:latest"])

    def test_chat_stream_collects_chunks(self):
        client = OllamaClient(self.url)
        seen = []
        full = client.chat_stream(
            "fake-a:latest", [{"role": "user", "content": "hi"}],
            seen.append, threading.Event())
        self.assertEqual(full, "Hello from A.")
        self.assertEqual(seen, ["Hello ", "from A."])

    def test_chat_stream_unknown_model_plain_error(self):
        client = OllamaClient(self.url)
        with self.assertRaises(OllamaError) as ctx:
            client.chat_stream(
                "nope:latest", [{"role": "user", "content": "hi"}],
                lambda t: None, threading.Event())
        self.assertNotIn("Traceback", str(ctx.exception))
        self.assertIn("couldn't be loaded", str(ctx.exception))

    def test_chat_stream_cancel_midway(self):
        client = OllamaClient(self.url)
        event = threading.Event()
        event.set()  # cancelled before the first chunk is read
        with self.assertRaises(Cancelled):
            client.chat_stream(
                "fake-a:latest", [{"role": "user", "content": "hi"}],
                lambda t: None, event)

    def test_ollama_down_plain_error(self):
        client = OllamaClient("http://127.0.0.1:9")  # nothing listens here
        with mock.patch("dual_chat.shutil.which", return_value=None):
            with self.assertRaises(OllamaError) as ctx:
                client.list_models()
        self.assertIn("isn't running", str(ctx.exception))
        with self.assertRaises(OllamaError) as ctx:
            client.chat_stream(
                "fake-a:latest", [{"role": "user", "content": "hi"}],
                lambda t: None, threading.Event())
        self.assertIn("isn't running", str(ctx.exception))

    def test_chat_stream_reports_metrics(self):
        client = OllamaClient(self.url)
        seen = {}
        full = client.chat_stream(
            "fake-a:latest", [{"role": "user", "content": "hi"}],
            lambda t: None, threading.Event(),
            on_metrics=seen.update)
        self.assertEqual(full, "Hello from A.")
        # The done chunk's timing fields, exactly as the server sent them.
        self.assertEqual(seen["eval_count"], 20)
        self.assertEqual(seen["eval_duration_ns"], 1000000000)
        self.assertEqual(seen["prompt_eval_duration_ns"], 200000000)
        self.assertEqual(seen["total_duration_ns"], 1500000000)
        # Client-side timing is measured, not canned: present, sane.
        self.assertIsNotNone(seen["ttft_s"])
        self.assertGreaterEqual(seen["ttft_s"], 0)
        self.assertIsNotNone(seen["total_s"])
        self.assertGreaterEqual(seen["total_s"], seen["ttft_s"])

    def test_running_models(self):
        client = OllamaClient(self.url)
        self.assertEqual(client.running_models(),
                         {"fake-a:latest": 1900000000,
                          "fake-b:latest": 1500000000})

    def test_running_models_down_plain_error(self):
        client = OllamaClient("http://127.0.0.1:9")  # nothing listens here
        with self.assertRaises(OllamaError) as ctx:
            client.running_models()
        self.assertIn("isn't running", str(ctx.exception))


# ---------------------------------------------------------------------------
# GUI tests (offscreen)
# ---------------------------------------------------------------------------

class FakeGuiClient:
    """Stands in for OllamaClient in the GUI tests.

    ``replies`` maps a model to a scripted list of replies, consumed in
    order (the last one repeats). ``messages_seen`` records every messages
    list the app sent, deep-copied, so relay tests can assert the exact
    text each model received. ``max_active`` proves generations never
    overlap. ``metrics`` maps a model to canned timing values reported
    through on_metrics (a default set is used when a model has none);
    ``ps`` maps a model name to its canned size in bytes for
    running_models ({} means nothing is loaded). Call reset() between
    tests.
    """

    DEFAULT_METRICS = {"eval_count": 10, "eval_duration_ns": 500000000,
                       "ttft_s": 0.25, "total_s": 0.75}

    def __init__(self):
        self.calls = []
        self.messages_seen = []
        self.replies = {}
        self.reply_index = {}
        self.metrics = {}
        self.ps = {}
        self.max_active = 0
        self._active = 0
        self.delay = 0.02  # seconds slept per word (raise to slow a turn)
        self.lock = threading.Lock()

    def reset(self):
        with self.lock:
            self.calls.clear()
            self.messages_seen.clear()
            self.replies.clear()
            self.reply_index.clear()
            self.metrics.clear()
            self.ps.clear()
            self.max_active = 0
            self._active = 0
            self.delay = 0.02

    def list_models(self, timeout=5):
        return ["fake-a:latest", "fake-b:latest"]

    def running_models(self, timeout=5):
        return dict(self.ps)

    def chat_stream(self, model, messages, on_chunk, cancel_event,
                    timeout=300, on_metrics=None):
        with self.lock:
            self.calls.append(model)
            self.messages_seen.append((model, copy.deepcopy(messages)))
            idx = self.reply_index.get(model, 0)
            script = self.replies.get(model)
            if script:
                text = script[min(idx, len(script) - 1)]
            else:
                text = "Reply from %s." % model
            self.reply_index[model] = idx + 1
            self._active += 1
            self.max_active = max(self.max_active, self._active)
        try:
            for word in text.split(" "):
                if cancel_event.is_set():
                    raise Cancelled()
                on_chunk(word + " ")
                time.sleep(self.delay)
        finally:
            with self.lock:
                self._active -= 1
        if on_metrics is not None:
            on_metrics(dict(self.metrics.get(model, self.DEFAULT_METRICS)))
        return text


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        self.window = build_window(self.client)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)

    def tearDown(self):
        self.window.close()

    def _wait_for(self, cond, timeout=10.0):
        from PySide6.QtTest import QTest
        deadline = time.time() + timeout
        while time.time() < deadline:
            QTest.qWait(50)
            if cond():
                return True
        return cond()

    def _pick_models(self):
        self.state["widgets"]["A"]["picker"].setCurrentIndex(0)
        self.state["widgets"]["B"]["picker"].setCurrentIndex(1)
        self.app.processEvents()

    def test_pickers_start_with_nothing_selected(self):
        # A fresh window, before any pick: both pickers blank, sends off.
        from dual_chat import build_window
        window = build_window(self.client)
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state["widgets"]["A"]["picker"].count() == 2))
            self.assertEqual(
                state["widgets"]["A"]["picker"].currentIndex(), -1)
            self.assertEqual(
                state["widgets"]["B"]["picker"].currentIndex(), -1)
            self.assertFalse(state["send_a"].isEnabled())
            self.assertFalse(state["send_b"].isEnabled())
            self.assertFalse(state["send_both"].isEnabled())
        finally:
            window.close()

    def test_send_buttons_follow_picks(self):
        self.state["widgets"]["A"]["picker"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(self.state["send_a"].isEnabled())
        self.assertFalse(self.state["send_b"].isEnabled())
        self.assertFalse(self.state["send_both"].isEnabled())
        self.state["widgets"]["B"]["picker"].setCurrentIndex(1)
        self.app.processEvents()
        self.assertTrue(self.state["send_both"].isEnabled())

    def test_window_fits_1366x768(self):
        size = self.window.size()
        self.assertLessEqual(size.width(), 1366)
        self.assertLessEqual(size.height(), 700)

    def test_send_to_both_replies_in_both_panes(self):
        self._pick_models()
        self.client.calls.clear()
        self.state["composer"].setPlainText("Hello, both of you.")
        self.state["send_both"].click()
        ok = self._wait_for(lambda: (
            "Reply from fake-a:latest." in
            self.state["widgets"]["A"]["transcript"].toPlainText()
            and "Reply from fake-b:latest." in
            self.state["widgets"]["B"]["transcript"].toPlainText()
            and len(self.state["convo"].sides["A"].history) == 2
            and len(self.state["convo"].sides["B"].history) == 2),
            timeout=15)
        self.assertTrue(ok, "both panes should show their replies")
        # One at a time, A first.
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest"])
        # Each side's history holds its own exchange only.
        convo = self.state["convo"]
        self.assertEqual(
            [m["content"] for m in convo.sides["A"].history],
            ["Hello, both of you.", "Reply from fake-a:latest."])
        self.assertEqual(
            [m["content"] for m in convo.sides["B"].history],
            ["Hello, both of you.", "Reply from fake-b:latest."])

    def test_send_to_a_only(self):
        self._pick_models()
        self.state["composer"].setPlainText("Just for A.")
        self.state["send_a"].click()
        ok = self._wait_for(lambda: (
            "Reply from fake-a:latest." in
            self.state["widgets"]["A"]["transcript"].toPlainText()
            and len(self.state["convo"].sides["A"].history) == 2),
            timeout=15)
        self.assertNotIn("Just for A.", self.state["widgets"]["B"]
                         ["transcript"].toPlainText())

    def test_clear_empties_one_side(self):
        self._pick_models()
        self.state["composer"].setPlainText("Hi A.")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(lambda: (
            "Reply from fake-a:latest." in
            self.state["widgets"]["A"]["transcript"].toPlainText()
            and len(self.state["convo"].sides["A"].history) == 2),
            timeout=15))
        self.state["widgets"]["A"]["clear"].click()
        self.assertEqual(self.state["convo"].sides["A"].history, [])
        self.assertEqual(self.state["widgets"]["A"]["transcript"]
                         .toPlainText(), "")

    def test_ollama_down_shows_plain_error(self):
        from dual_chat import OllamaError, build_window

        class DownClient:
            def list_models(self, timeout=5):
                raise OllamaError("Ollama isn't running.")

            def chat_stream(self, *a, **k):
                raise OllamaError("Ollama isn't running.")

        window = build_window(DownClient())
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(lambda:
                "isn't running" in window.statusBar().currentMessage(),
                timeout=10))
        finally:
            window.close()


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class RelayGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        self.client.reset()
        self.window = build_window(self.client)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self._pick_models()

    def tearDown(self):
        self.window.close()

    def _wait_for(self, cond, timeout=10.0):
        from PySide6.QtTest import QTest
        deadline = time.time() + timeout
        while time.time() < deadline:
            QTest.qWait(50)
            if cond():
                return True
        return cond()

    def _pick_models(self):
        self.state["widgets"]["A"]["picker"].setCurrentIndex(0)
        self.state["widgets"]["B"]["picker"].setCurrentIndex(1)
        self.app.processEvents()

    def _transcript(self, side):
        return self.state["widgets"][side]["transcript"].toPlainText()

    def _start_relay(self, seed, cap=10, mode="Blind", stop_text="",
                     agreement=False, start_side="A"):
        self.state["start_picker"].setCurrentIndex(
            0 if start_side == "A" else 1)
        self.state["cap_spin"].setValue(cap)
        self.state["mode_picker"].setCurrentIndex(
            0 if mode == "Blind" else 1)
        self.state["stop_edit"].setText(stop_text)
        self.state["agree_check"].setChecked(agreement)
        self.state["composer"].setPlainText(seed)
        self.app.processEvents()
        self.state["relay_btn"].click()

    def _wait_for_relay_stopped(self, timeout=15.0):
        return self._wait_for(lambda: self.state["relay_status"].text()
                              .startswith("Relay stopped"), timeout=timeout)

    def test_relay_button_needs_both_models(self):
        from dual_chat import build_window
        window = build_window(self.client)
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state["widgets"]["A"]["picker"].count() == 2))
            self.assertFalse(state["relay_btn"].isEnabled())
            state["widgets"]["A"]["picker"].setCurrentIndex(0)
            self.app.processEvents()
            self.assertFalse(state["relay_btn"].isEnabled())
            state["widgets"]["B"]["picker"].setCurrentIndex(1)
            self.app.processEvents()
            self.assertTrue(state["relay_btn"].isEnabled())
        finally:
            window.close()

    def test_relay_needs_seed_text(self):
        self.state["relay_btn"].click()
        self.app.processEvents()
        self.assertEqual(self.state["relay_status"].text(), "Relay off.")
        self.assertEqual(self.client.calls, [])

    def test_blind_relay_delivers_bare_text(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self._start_relay("Talk about fruit.", cap=2, mode="Blind")
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest"])
        # B received the bare reply text -- no framing, no mention of AI.
        model, messages = self.client.messages_seen[1]
        self.assertEqual(model, "fake-b:latest")
        self.assertEqual(messages, [{"role": "user", "content": "apple pie"}])
        self.assertNotIn("AI", messages[0]["content"])
        self.assertNotIn("assistant", messages[0]["content"].lower())

    def test_aware_relay_adds_framing(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self._start_relay("Talk about fruit.", cap=3, mode="Aware")
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest", "fake-a:latest"])
        _, messages_b = self.client.messages_seen[1]
        self.assertEqual(messages_b, [
            {"role": "user",
             "content": AWARE_TEMPLATE % ("A", "apple pie")}])
        # The hop back names Model B.
        _, messages_a2 = self.client.messages_seen[2]
        self.assertEqual(messages_a2[-1], {
            "role": "user",
            "content": AWARE_TEMPLATE % ("B", "banana split")})

    def test_turn_cap_halts_at_exactly_n(self):
        self.client.replies = {"fake-a:latest": ["a one", "a two"],
                               "fake-b:latest": ["b one", "b two"]}
        self._start_relay("Count with me.", cap=4)
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest",
                          "fake-a:latest", "fake-b:latest"])
        self.assertIn("turn cap reached",
                      self.state["relay_status"].text())
        # Histories accumulate the relayed exchanges.
        _, messages_b2 = self.client.messages_seen[3]
        self.assertEqual(messages_b2, [
            {"role": "user", "content": "a one"},
            {"role": "assistant", "content": "b one"},
            {"role": "user", "content": "a two"},
        ])

    def test_stop_phrase_halts_and_names_phrase(self):
        self.client.replies = {"fake-a:latest": ["I love pineapple pizza"]}
        self._start_relay("Talk about food.", cap=10,
                          stop_text="pineapple, anchovies")
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        self.assertIn("pineapple", self.state["relay_status"].text())
        self.assertIn("pineapple", self._transcript("A"))

    def test_agreement_stop_halts(self):
        self.client.replies = {
            "fake-a:latest": ["What do you think?"],
            "fake-b:latest": ["Sounds good, let's do it"]}
        self._start_relay("Decide something.", cap=10, agreement=True)
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest"])
        self.assertIn("agreement", self.state["relay_status"].text())

    def test_no_agreement_stop_when_disabled(self):
        self.client.replies = {
            "fake-a:latest": ["What do you think?"],
            "fake-b:latest": ["Sounds good, let's do it"]}
        self._start_relay("Decide something.", cap=3, agreement=False)
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertEqual(len(self.client.calls), 3)
        self.assertIn("turn cap reached",
                      self.state["relay_status"].text())

    def test_stop_button_halts_mid_loop(self):
        self.client.replies = {"fake-a:latest": [" ".join(["word"] * 200)]}
        self._start_relay("Go on and on.", cap=50)
        self.assertTrue(self._wait_for(
            lambda: "word" in self._transcript("A")), "first turn started")
        self.assertEqual(self.state["relay_btn"].text(), "Stop Relay")
        self.state["relay_btn"].click()
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertIn("stopped by you", self.state["relay_status"].text())
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        # Nothing else starts after the stop.
        time.sleep(1.0)
        self.app.processEvents()
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        self.assertFalse(self.state["state"]["busy"])

    def test_relay_runs_one_at_a_time(self):
        self.client.replies = {"fake-a:latest": ["a one", "a two"],
                               "fake-b:latest": ["b one", "b two"]}
        self._start_relay("Count with me.", cap=4)
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertEqual(self.client.max_active, 1)
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest",
                          "fake-a:latest", "fake-b:latest"])

    def test_relay_tags_in_transcript_only(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self._start_relay("Talk about fruit.", cap=2)
        self.assertTrue(self._wait_for_relay_stopped())
        # His window tags the relayed hop...
        self.assertIn("[relay from Model A]", self._transcript("B"))
        # ...but the tag never reaches either model's history.
        convo = self.state["convo"]
        for side in ("A", "B"):
            for message in convo.sides[side].history:
                self.assertNotIn("[relay", message["content"])
        # And the seed itself carries no tag.
        self.assertNotIn("[relay", self._transcript("A"))

    def test_relay_can_start_with_b(self):
        self.client.replies = {"fake-b:latest": ["starting from B"]}
        self._start_relay("You start.", cap=1, start_side="B")
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertEqual(self.client.calls, ["fake-b:latest"])
        self.assertIn("starting from B", self._transcript("B"))
        self.assertEqual(self._transcript("A"), "")

    def test_relay_status_shows_live_turns(self):
        self.client.replies = {"fake-a:latest": [" ".join(["word"] * 100)],
                               "fake-b:latest": ["short"]}
        self._start_relay("Go on.", cap=10)
        self.assertTrue(self._wait_for(
            lambda: self.state["relay_status"].text().startswith(
                "Relay on: turn 1/10, you to A")))
        self.state["relay_btn"].click()
        self.assertTrue(self._wait_for_relay_stopped())

    def test_close_window_mid_relay_is_quiet(self):
        # Closing while a relay runs must not crash or raise: queued
        # runner signals landing after the window is gone are ignored.
        from dual_chat import build_window
        window = build_window(self.client)
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state["widgets"]["A"]["picker"].count() == 2))
            state["widgets"]["A"]["picker"].setCurrentIndex(0)
            state["widgets"]["B"]["picker"].setCurrentIndex(1)
            self.client.replies = {
                "fake-a:latest": [" ".join(["word"] * 200)]}
            state["composer"].setPlainText("Go on and on.")
            state["cap_spin"].setValue(50)
            self.app.processEvents()
            state["relay_btn"].click()
            self.assertTrue(self._wait_for(
                lambda: "word" in state["widgets"]["A"]["transcript"]
                .toPlainText()), "first turn started")
        finally:
            window.close()
        # Give the queued signals time to land on the closed window.
        self._wait_for(lambda: False, timeout=1.0)


# ---------------------------------------------------------------------------
# ChatSide editing: reorder, delete, labels, instructions (pure, no Qt)
# ---------------------------------------------------------------------------

class ChatSideEditTests(unittest.TestCase):
    def _side_with_four(self):
        side = ChatSide("A")
        side.model = "fake-a:latest"
        side.add_user("first")
        side.add_assistant("alpha one")
        side.add_user("second")
        side.add_assistant("alpha two")
        return side

    def _contents(self, side):
        return [m["content"] for m in side.history]

    def test_labels_default(self):
        side = ChatSide("A")
        side.model = "fake-a:latest"
        side.add_user("hi")
        side.add_assistant("hello")
        self.assertEqual(side.labels, ["You", "fake-a:latest"])
        side2 = ChatSide("B")
        side2.add_assistant("hello")
        self.assertEqual(side2.labels, ["Assistant"])

    def test_labels_move_with_entries(self):
        side = self._side_with_four()
        side.move_up(2)
        self.assertEqual(self._contents(side),
                         ["first", "second", "alpha one", "alpha two"])
        self.assertEqual(side.labels,
                         ["You", "You", "fake-a:latest", "fake-a:latest"])

    def test_move_up_and_down(self):
        side = self._side_with_four()
        side.move_up(1)
        self.assertEqual(self._contents(side),
                         ["alpha one", "first", "second", "alpha two"])
        side.move_down(0)
        self.assertEqual(self._contents(side),
                         ["first", "alpha one", "second", "alpha two"])
        side.move_down(3)  # last entry: no-op
        self.assertEqual(self._contents(side),
                         ["first", "alpha one", "second", "alpha two"])
        side.move_up(0)  # first entry: no-op
        self.assertEqual(self._contents(side),
                         ["first", "alpha one", "second", "alpha two"])

    def test_move_out_of_range_is_noop(self):
        side = self._side_with_four()
        side.move_up(-1)
        side.move_up(99)
        side.move_down(-1)
        side.move_down(99)
        self.assertEqual(self._contents(side),
                         ["first", "alpha one", "second", "alpha two"])

    def test_delete_removes_entry_and_label(self):
        side = self._side_with_four()
        side.delete_at(1)
        self.assertEqual(self._contents(side),
                         ["first", "second", "alpha two"])
        self.assertEqual(side.labels, ["You", "You", "fake-a:latest"])
        side.delete_at(99)  # out of range: no-op
        side.delete_at(-1)
        self.assertEqual(len(side.history), 3)

    def test_clear_clears_labels_too(self):
        side = self._side_with_four()
        side.clear()
        self.assertEqual(side.history, [])
        self.assertEqual(side.labels, [])

    def test_messages_for_model_without_instruction(self):
        side = self._side_with_four()
        self.assertEqual(side.messages_for_model(), side.history)
        self.assertIsNot(side.messages_for_model(), side.history)

    def test_messages_for_model_with_instruction(self):
        side = self._side_with_four()
        side.instruction = "Be brief."
        self.assertEqual(side.messages_for_model(), [
            {"role": "system", "content": "Be brief."},
        ] + side.history)

    def test_messages_for_model_whitespace_instruction_is_none(self):
        side = self._side_with_four()
        side.instruction = "   \n  "
        self.assertEqual(side.messages_for_model(), side.history)


# ---------------------------------------------------------------------------
# Summarize: fold older turns into one summary entry (pure, no Qt)
# ---------------------------------------------------------------------------

class SummarizeTests(unittest.TestCase):
    def _side_with_six(self):
        side = ChatSide("A")
        side.model = "fake-a:latest"
        side.add_user("first")
        side.add_assistant("alpha one")
        side.add_user("second")
        side.add_assistant("alpha two")
        side.add_user("third")
        side.add_assistant("alpha three")
        return side

    def test_summarize_keeps_last_four_verbatim(self):
        side = self._side_with_six()
        self.assertTrue(side.summarize("They talked about fruit."))
        self.assertEqual(
            [m["content"] for m in side.history],
            ["Earlier in this conversation: They talked about fruit.",
             "second", "alpha two", "third", "alpha three"])
        self.assertEqual(side.labels, [
            SUMMARY_LABEL, "You", "fake-a:latest", "You", "fake-a:latest"])
        self.assertEqual(side.history[0]["role"], "user")
        self.assertEqual(SUMMARY_KEEP_LAST, 4)

    def test_summarize_short_history_is_noop(self):
        side = ChatSide("A")
        side.add_user("hi")
        side.add_assistant("hello")
        self.assertFalse(side.summarize("whatever"))
        self.assertEqual(len(side.history), 2)
        # Exactly 4 entries: nothing older to fold.
        side.add_user("again")
        side.add_assistant("yep")
        self.assertFalse(side.summarize("whatever"))
        self.assertEqual(len(side.history), 4)

    def test_summarize_five_entries_folds_one(self):
        side = self._side_with_six()
        side.delete_at(0)
        self.assertEqual(len(side.history), 5)
        self.assertTrue(side.summarize("Summary."))
        self.assertEqual(
            [m["content"] for m in side.history],
            ["Earlier in this conversation: Summary.",
             "second", "alpha two", "third", "alpha three"])

    def test_summarize_empty_summary_is_noop(self):
        side = self._side_with_six()
        self.assertFalse(side.summarize("   "))
        self.assertEqual(len(side.history), 6)

    def test_second_summarize_folds_the_first(self):
        side = self._side_with_six()
        side.summarize("First summary.")
        side.add_user("fourth")
        side.add_assistant("alpha four")
        self.assertEqual(len(side.history), 7)
        self.assertTrue(side.summarize("Second summary."))
        self.assertEqual(
            [m["content"] for m in side.history],
            ["Earlier in this conversation: Second summary.",
             "third", "alpha three", "fourth", "alpha four"])
        # One summary entry, not two: the old one was folded in.
        self.assertEqual(side.labels.count(SUMMARY_LABEL), 1)

    def test_history_chars_sums_contents(self):
        side = ChatSide("A")
        self.assertEqual(side.history_chars(), 0)
        side.add_user("abc")
        side.add_assistant("de")
        self.assertEqual(side.history_chars(), 5)

    def test_budget_constant(self):
        self.assertEqual(HISTORY_BUDGET_CHARS, 12000)


# ---------------------------------------------------------------------------
# Turn stats: math, formatting, /api/ps parsing (pure, no Qt)
# ---------------------------------------------------------------------------

class StatsMathTests(unittest.TestCase):
    def test_compute_tok_per_sec_exact(self):
        stats = compute_turn_stats({
            "eval_count": 40, "eval_duration_ns": 2000000000,
            "ttft_s": 0.5, "total_s": 2.5})
        self.assertEqual(stats, {"tok_per_sec": 20.0,
                                 "ttft_s": 0.5, "total_s": 2.5})

    def test_compute_missing_fields_are_none(self):
        self.assertEqual(compute_turn_stats({}),
                         {"tok_per_sec": None, "ttft_s": None,
                          "total_s": None})
        # Zero duration: no division, no invented speed.
        self.assertIsNone(compute_turn_stats(
            {"eval_count": 40, "eval_duration_ns": 0})["tok_per_sec"])
        self.assertIsNone(compute_turn_stats(
            {"eval_count": 40})["tok_per_sec"])
        self.assertIsNone(compute_turn_stats(
            {"eval_duration_ns": 1000})["tok_per_sec"])
        self.assertIsNone(compute_turn_stats(
            {"ttft_s": "fast"})["ttft_s"])

    def test_format_stats_line_exact(self):
        self.assertEqual(format_stats_line(None),
                         "-- tok/s · first token -- · turn -- · RAM --")
        self.assertEqual(format_stats_line({
            "tok_per_sec": 20.0, "ttft_s": 0.5, "total_s": 2.5,
            "ram": "1.9 GB in RAM"}),
            "20.0 tok/s · first token 0.50s · turn 2.5s · 1.9 GB in RAM")
        self.assertEqual(format_stats_line({
            "tok_per_sec": None, "ttft_s": None, "total_s": None,
            "ram": "not loaded"}),
            "tok/s n/a · first token n/a · turn n/a · not loaded")
        self.assertEqual(format_stats_line({
            "tok_per_sec": 12.345, "ttft_s": 1.0, "total_s": 10.0,
            "ram": None}),
            "12.3 tok/s · first token 1.00s · turn 10.0s · RAM n/a")

    def test_parse_ps_payload(self):
        self.assertEqual(parse_ps_payload(
            {"models": [{"name": "a:2b", "size": 1900000000},
                        {"name": "b:2b", "size": 512000000}]}),
            {"a:2b": 1900000000, "b:2b": 512000000})
        self.assertEqual(parse_ps_payload({}), {})
        self.assertEqual(parse_ps_payload({"models": []}), {})
        self.assertEqual(parse_ps_payload({"models": [{"name": "a:2b"}]}), {})
        self.assertEqual(parse_ps_payload({"models": [{"size": 5}]}), {})

    def test_format_ram(self):
        self.assertEqual(format_ram(1900000000), "1.9 GB")
        self.assertEqual(format_ram(1500000000), "1.5 GB")
        self.assertEqual(format_ram(512000000), "512 MB")
        self.assertEqual(format_ram(1000000000), "1.0 GB")


# ---------------------------------------------------------------------------
# Saved pairs: JSON file round-trip (pure, no Qt)
# ---------------------------------------------------------------------------

class PairsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "duo-pairs.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(load_pairs(self.path), {})

    def test_corrupt_file_reads_as_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(load_pairs(self.path), {})
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"pairs": ["not", "a", "dict"]}, fh)
        self.assertEqual(load_pairs(self.path), {})

    def test_save_load_round_trip_exact_contents(self):
        setup = {"model_a": "fake-a:latest", "model_b": "fake-b:latest",
                 "instruction_a": "Be brief.", "instruction_b": ""}
        save_pair(self.path, "work pair", setup)
        with open(self.path, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk, {"pairs": {"work pair": setup}})
        self.assertEqual(load_pairs(self.path), {"work pair": setup})

    def test_save_keeps_other_pairs_and_overwrites(self):
        save_pair(self.path, "one", {"model_a": "a", "model_b": None,
                                     "instruction_a": "", "instruction_b": ""})
        save_pair(self.path, "two", {"model_a": "b", "model_b": "c",
                                     "instruction_a": "x", "instruction_b": "y"})
        save_pair(self.path, "one", {"model_a": "z", "model_b": None,
                                     "instruction_a": "", "instruction_b": ""})
        pairs = load_pairs(self.path)
        self.assertEqual(set(pairs), {"one", "two"})
        self.assertEqual(pairs["one"]["model_a"], "z")
        self.assertEqual(pairs["two"]["model_a"], "b")

    def test_pairs_path_env_override(self):
        with mock.patch.dict(os.environ,
                             {"DUAL_CHAT_PAIRS_FILE": "/tmp/elsewhere.json"}):
            self.assertEqual(pairs_path(), "/tmp/elsewhere.json")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(pairs_path().endswith("duo-pairs.json"))


# ---------------------------------------------------------------------------
# Director controls GUI tests (offscreen)
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class DirectorGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        self.client.reset()
        # Pairs go to a temp file so tests never touch the real one.
        self.tmp = tempfile.TemporaryDirectory()
        self.pairs_file = os.path.join(self.tmp.name, "duo-pairs.json")
        self._env = mock.patch.dict(
            os.environ, {"DUAL_CHAT_PAIRS_FILE": self.pairs_file})
        self._env.start()
        self.window = build_window(self.client)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self._pick_models()

    def tearDown(self):
        self.window.close()
        self._env.stop()
        self.tmp.cleanup()

    def _wait_for(self, cond, timeout=10.0):
        from PySide6.QtTest import QTest
        deadline = time.time() + timeout
        while time.time() < deadline:
            QTest.qWait(50)
            if cond():
                return True
        return cond()

    def _pick_models(self):
        self.state["widgets"]["A"]["picker"].setCurrentIndex(0)
        self.state["widgets"]["B"]["picker"].setCurrentIndex(1)
        self.app.processEvents()

    def _transcript(self, side):
        return self.state["widgets"][side]["transcript"].toPlainText()

    def _send_to_a_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_a"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _last_messages_for(self, model):
        for seen_model, messages in reversed(self.client.messages_seen):
            if seen_model == model:
                return messages
        return None

    def _open_history(self, side):
        w = self.state["widgets"][side]
        if not w["hist_toggle"].isChecked():
            w["hist_toggle"].click()
        self.app.processEvents()
        self.assertTrue(w["hist_frame"].isVisible())

    def test_reorder_changes_what_the_model_sees(self):
        self.client.replies = {"fake-a:latest": ["alpha one", "alpha two"]}
        self.assertTrue(self._send_to_a_and_wait("first message"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        convo = self.state["convo"]
        self.assertEqual(
            [m["content"] for m in convo.sides["A"].history],
            ["first message", "alpha one", "second message", "alpha two"])
        self._open_history("A")
        lst = self.state["widgets"]["A"]["hist_list"]
        self.assertEqual(lst.count(), 4)
        lst.setCurrentRow(2)  # "You: second message"
        self.app.processEvents()
        self.assertTrue(self.state["widgets"]["A"]["hist_up"].isEnabled())
        self.state["widgets"]["A"]["hist_up"].click()
        self.app.processEvents()
        # History and transcript both show the new order.
        self.assertEqual(
            [m["content"] for m in convo.sides["A"].history],
            ["first message", "second message", "alpha one", "alpha two"])
        text = self._transcript("A")
        self.assertLess(text.find("first message"),
                        text.find("second message"))
        self.assertLess(text.find("second message"),
                        text.find("alpha one"))
        self.assertLess(text.find("alpha one"), text.find("alpha two"))
        # The next turn sends the reordered history, exactly.
        self.assertTrue(self._send_to_a_and_wait("third message"))
        self.assertEqual(self._last_messages_for("fake-a:latest"), [
            {"role": "user", "content": "first message"},
            {"role": "user", "content": "second message"},
            {"role": "assistant", "content": "alpha one"},
            {"role": "assistant", "content": "alpha two"},
            {"role": "user", "content": "third message"},
        ])

    def test_delete_removes_message_from_history(self):
        self.client.replies = {"fake-a:latest": ["alpha one", "alpha two"]}
        self.assertTrue(self._send_to_a_and_wait("first message"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        self._open_history("A")
        lst = self.state["widgets"]["A"]["hist_list"]
        lst.setCurrentRow(0)  # "You: first message"
        self.app.processEvents()
        self.state["widgets"]["A"]["hist_delete"].click()
        self.app.processEvents()
        convo = self.state["convo"]
        self.assertEqual(
            [m["content"] for m in convo.sides["A"].history],
            ["alpha one", "second message", "alpha two"])
        self.assertNotIn("first message", self._transcript("A"))
        self.assertTrue(self._send_to_a_and_wait("third message"))
        self.assertEqual(self._last_messages_for("fake-a:latest"), [
            {"role": "assistant", "content": "alpha one"},
            {"role": "user", "content": "second message"},
            {"role": "assistant", "content": "alpha two"},
            {"role": "user", "content": "third message"},
        ])

    def test_history_buttons_follow_selection(self):
        self.assertTrue(self._send_to_a_and_wait("hello"))
        self._open_history("A")
        w = self.state["widgets"]["A"]
        lst = w["hist_list"]
        self.assertFalse(w["hist_up"].isEnabled())
        self.assertFalse(w["hist_down"].isEnabled())
        self.assertFalse(w["hist_delete"].isEnabled())
        lst.setCurrentRow(0)
        self.app.processEvents()
        self.assertFalse(w["hist_up"].isEnabled())
        self.assertTrue(w["hist_down"].isEnabled())
        self.assertTrue(w["hist_delete"].isEnabled())
        lst.setCurrentRow(1)
        self.app.processEvents()
        self.assertTrue(w["hist_up"].isEnabled())
        self.assertFalse(w["hist_down"].isEnabled())

    def _start_review_relay(self, seed, cap=3):
        # Note: no client.reset() here -- the caller sets scripted
        # replies first, and reset() would wipe them.
        self.state["review_check"].setChecked(True)
        self.state["start_picker"].setCurrentIndex(0)
        self.state["cap_spin"].setValue(cap)
        self.state["mode_picker"].setCurrentIndex(0)  # Blind
        self.state["composer"].setPlainText(seed)
        self.app.processEvents()
        self.state["relay_btn"].click()

    def _wait_for_review(self, timeout=15.0):
        return self._wait_for(
            lambda: self.state["review_frame"].isVisible()
            and self.state["state"].get("pending_hop") is not None,
            timeout=timeout)

    def _wait_for_relay_stopped(self, timeout=15.0):
        return self._wait_for(lambda: self.state["relay_status"].text()
                              .startswith("Relay stopped"), timeout=timeout)

    def test_review_forwards_edited_text(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self._start_review_relay("Talk about fruit.", cap=3)
        self.assertTrue(self._wait_for_review())
        # Blind mode: the review box shows the bare reply text.
        self.assertEqual(self.state["review_edit"].toPlainText(), "apple pie")
        self.assertIn("Model A to Model B",
                      self.state["review_label"].text())
        # Nothing was forwarded yet: B has no job.
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        self.state["review_edit"].setPlainText("apple crumble")
        self.state["review_forward_edited"].click()
        # B replies, then the relay pauses again before the hop back.
        self.assertTrue(self._wait_for_review())
        self.assertEqual(self.state["review_edit"].toPlainText(),
                         "banana split")
        self.state["review_forward"].click()
        self.assertTrue(self._wait_for_relay_stopped())
        # B received the EDITED text, and only the edited text.
        model, messages = self.client.messages_seen[1]
        self.assertEqual(model, "fake-b:latest")
        self.assertEqual(messages, [{"role": "user", "content": "apple crumble"}])
        # The hop back carried B's reply unchanged.
        _, messages_a2 = self.client.messages_seen[2]
        self.assertEqual(messages_a2[-1],
                         {"role": "user", "content": "banana split"})
        # His window shows the edited text with the relay tag.
        self.assertIn("apple crumble", self._transcript("B"))
        self.assertIn("[relay from Model A]", self._transcript("B"))

    def test_review_forward_ignores_edits(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self._start_review_relay("Talk about fruit.", cap=2)
        self.assertTrue(self._wait_for_review())
        self.state["review_edit"].setPlainText("CHANGED")
        self.state["review_forward"].click()
        self.assertTrue(self._wait_for_relay_stopped())
        _, messages = self.client.messages_seen[1]
        self.assertEqual(messages, [{"role": "user", "content": "apple pie"}])

    def test_review_skip_ends_relay(self):
        self.client.replies = {"fake-a:latest": ["apple pie"]}
        self._start_review_relay("Talk about fruit.", cap=10)
        self.assertTrue(self._wait_for_review())
        self.state["review_skip"].click()
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertIn("skipped", self.state["relay_status"].text())
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        self.assertFalse(self.state["review_frame"].isVisible())
        self.assertFalse(self.state["state"]["busy"])

    def test_review_stop_ends_relay_and_next_send_still_works(self):
        self.client.replies = {"fake-a:latest": ["apple pie"]}
        self._start_review_relay("Talk about fruit.", cap=10)
        self.assertTrue(self._wait_for_review())
        self.state["review_stop"].click()
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertIn("stopped by you", self.state["relay_status"].text())
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        # The stop during a review pause must not poison the next job
        # for that side: a plain send still runs to completion.
        self.state["review_check"].setChecked(False)
        self.assertTrue(self._send_to_a_and_wait("are you still there?"))
        self.assertEqual(self.client.calls.count("fake-a:latest"), 2)

    def test_instructions_sent_as_system_message(self):
        self.state["widgets"]["A"]["instr_edit"].setPlainText(
            "You are a pirate.")
        self.state["composer"].setPlainText("Hello")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(
            lambda: len(self.client.messages_seen) >= 1, timeout=15))
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self.assertEqual(self._last_messages_for("fake-a:latest"), [
            {"role": "system", "content": "You are a pirate."},
            {"role": "user", "content": "Hello"},
        ])
        # B has no instruction: no system message at all.
        self.state["composer"].setPlainText("Hello")
        self.state["send_b"].click()
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self.assertEqual(self._last_messages_for("fake-b:latest"), [
            {"role": "user", "content": "Hello"},
        ])
        # A whitespace-only instruction is the same as none.
        self.state["widgets"]["B"]["instr_edit"].setPlainText("   ")
        self.state["composer"].setPlainText("Hello again")
        self.state["send_b"].click()
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        messages = self._last_messages_for("fake-b:latest")
        self.assertTrue(all(m["role"] != "system" for m in messages))

    def test_pairs_save_load_round_trip(self):
        self.state["widgets"]["A"]["instr_edit"].setPlainText("Be brief.")
        self.state["widgets"]["B"]["instr_edit"].setPlainText("Be thorough.")
        self.state["pair_name"].setText("work pair")
        self.state["pair_save"].click()
        self.app.processEvents()
        with open(self.pairs_file, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk, {"pairs": {"work pair": {
            "model_a": "fake-a:latest", "model_b": "fake-b:latest",
            "instruction_a": "Be brief.",
            "instruction_b": "Be thorough."}}})
        combo = self.state["pair_combo"]
        self.assertEqual([combo.itemText(i) for i in range(combo.count())],
                         ["work pair"])
        # Change everything, then load the pair back from the dropdown.
        self.state["widgets"]["A"]["picker"].setCurrentIndex(1)
        self.state["widgets"]["B"]["picker"].setCurrentIndex(0)
        self.state["widgets"]["A"]["instr_edit"].setPlainText("")
        self.state["widgets"]["B"]["instr_edit"].setPlainText("")
        self.app.processEvents()
        combo.setCurrentIndex(-1)
        combo.setCurrentIndex(0)
        self.app.processEvents()
        convo = self.state["convo"]
        self.assertEqual(convo.sides["A"].model, "fake-a:latest")
        self.assertEqual(convo.sides["B"].model, "fake-b:latest")
        self.assertEqual(
            self.state["widgets"]["A"]["instr_edit"].toPlainText(),
            "Be brief.")
        self.assertEqual(
            self.state["widgets"]["B"]["instr_edit"].toPlainText(),
            "Be thorough.")
        # Re-picking the pair that is already selected still loads it:
        # change the setup again and activate the same dropdown item.
        self.state["widgets"]["A"]["picker"].setCurrentIndex(1)
        self.state["widgets"]["A"]["instr_edit"].setPlainText("Changed.")
        self.app.processEvents()
        combo.activated.emit(0)
        self.app.processEvents()
        self.assertEqual(convo.sides["A"].model, "fake-a:latest")
        self.assertEqual(
            self.state["widgets"]["A"]["instr_edit"].toPlainText(),
            "Be brief.")

    def test_pairs_nothing_loaded_by_default(self):
        save_pair(self.pairs_file, "work pair",
                  {"model_a": "fake-a:latest", "model_b": "fake-b:latest",
                   "instruction_a": "Be brief.", "instruction_b": ""})
        from dual_chat import build_window
        window = build_window(self.client)
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state["widgets"]["A"]["picker"].count() == 2))
            combo = state["pair_combo"]
            self.assertEqual(
                [combo.itemText(i) for i in range(combo.count())],
                ["work pair"])
            # Listed, but not loaded: pickers still blank, no models set.
            self.assertEqual(
                state["widgets"]["A"]["picker"].currentIndex(), -1)
            self.assertEqual(
                state["widgets"]["B"]["picker"].currentIndex(), -1)
            self.assertIsNone(state["convo"].sides["A"].model)
            self.assertIsNone(state["convo"].sides["B"].model)
        finally:
            window.close()

    def test_pair_save_needs_a_name(self):
        self.state["pair_name"].setText("   ")
        self.state["pair_save"].click()
        self.app.processEvents()
        self.assertFalse(os.path.exists(self.pairs_file))
        self.assertIn("Name the pair",
                      self.window.statusBar().currentMessage())

    def test_compare_use_this_sends_pick_to_other_side(self):
        self.client.replies = {"fake-a:latest": ["answer alpha"],
                               "fake-b:latest": ["answer beta"]}
        use_a = self.state["widgets"]["A"]["use_this"]
        use_b = self.state["widgets"]["B"]["use_this"]
        self.assertFalse(use_a.isEnabled())
        self.assertFalse(use_b.isEnabled())
        self.state["composer"].setPlainText("Which is better?")
        self.state["send_both"].click()
        self.assertTrue(self._wait_for(
            lambda: use_a.isEnabled() and use_b.isEnabled(), timeout=15))
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest"])
        use_a.click()
        self.assertTrue(self._wait_for(
            lambda: "You picked this answer: answer alpha" in
            self._transcript("B") and not self.state["state"]["busy"],
            timeout=15))
        # Only B got a new job, carrying the picked reply.
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest", "fake-b:latest"])
        messages = self._last_messages_for("fake-b:latest")
        self.assertEqual(messages[-1], {
            "role": "user",
            "content": COMPARE_PREFIX % "answer alpha"})
        # The picked pane is marked, and the offer is spent.
        self.assertIn("(Picked for Model B.)", self._transcript("A"))
        self.assertFalse(use_a.isEnabled())
        self.assertFalse(use_b.isEnabled())

    def test_compare_offer_ends_on_new_send(self):
        self.client.replies = {"fake-a:latest": ["answer alpha"],
                               "fake-b:latest": ["answer beta"]}
        self.state["composer"].setPlainText("Which is better?")
        self.state["send_both"].click()
        use_a = self.state["widgets"]["A"]["use_this"]
        self.assertTrue(self._wait_for(lambda: use_a.isEnabled(),
                                       timeout=15))
        self.state["composer"].setPlainText("Something else.")
        self.state["send_a"].click()
        self.app.processEvents()
        self.assertFalse(use_a.isEnabled())
        self.assertFalse(
            self.state["widgets"]["B"]["use_this"].isEnabled())
        # Let the new send finish before the window closes: the runner
        # is a daemon thread, and a job left running would fire into
        # the next test's shared fake client.
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))


# ---------------------------------------------------------------------------
# Item 4 GUI tests: summarize, budget nudge, turn stats (offscreen)
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class Item4GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        self.client.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.pairs_file = os.path.join(self.tmp.name, "duo-pairs.json")
        self._env = mock.patch.dict(
            os.environ, {"DUAL_CHAT_PAIRS_FILE": self.pairs_file})
        self._env.start()
        self.window = build_window(self.client)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self._pick_models()

    def tearDown(self):
        self.window.close()
        self._env.stop()
        self.tmp.cleanup()

    def _wait_for(self, cond, timeout=10.0):
        from PySide6.QtTest import QTest
        deadline = time.time() + timeout
        while time.time() < deadline:
            QTest.qWait(50)
            if cond():
                return True
        return cond()

    def _pick_models(self):
        self.state["widgets"]["A"]["picker"].setCurrentIndex(0)
        self.state["widgets"]["B"]["picker"].setCurrentIndex(1)
        self.app.processEvents()

    def _transcript(self, side):
        return self.state["widgets"][side]["transcript"].toPlainText()

    def _stats(self, side):
        return self.state["widgets"][side]["stats"].text()

    def _send_to_a_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_a"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _last_messages_for(self, model):
        for seen_model, messages in reversed(self.client.messages_seen):
            if seen_model == model:
                return messages
        return None

    def _start_relay(self, seed, cap=10):
        self.state["start_picker"].setCurrentIndex(0)
        self.state["cap_spin"].setValue(cap)
        self.state["mode_picker"].setCurrentIndex(0)  # Blind
        self.state["composer"].setPlainText(seed)
        self.app.processEvents()
        self.state["relay_btn"].click()

    def _wait_for_relay_stopped(self, timeout=15.0):
        return self._wait_for(lambda: self.state["relay_status"].text()
                              .startswith("Relay stopped"), timeout=timeout)

    def _three_exchanges(self):
        self.client.replies = {"fake-a:latest": [
            "alpha one", "alpha two", "alpha three"]}
        self.assertTrue(self._send_to_a_and_wait("first message"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        self.assertTrue(self._send_to_a_and_wait("third message"))
        self.assertEqual(len(self.state["convo"].sides["A"].history), 6)

    def test_stats_show_dashes_before_any_turn(self):
        dashes = "-- tok/s · first token -- · turn -- · RAM --"
        self.assertEqual(self._stats("A"), dashes)
        self.assertEqual(self._stats("B"), dashes)

    def test_stats_after_turn_show_exact_canned_values(self):
        self.client.metrics = {"fake-a:latest": {
            "eval_count": 40, "eval_duration_ns": 2000000000,
            "ttft_s": 0.5, "total_s": 2.5}}
        self.client.ps = {"fake-a:latest": 1900000000}
        self.assertTrue(self._send_to_a_and_wait("Hello"))
        self.assertEqual(
            self._stats("A"),
            "20.0 tok/s · first token 0.50s · turn 2.5s · 1.9 GB in RAM")
        # B hasn't run: still dashes.
        self.assertEqual(
            self._stats("B"),
            "-- tok/s · first token -- · turn -- · RAM --")

    def test_stats_ram_not_loaded(self):
        self.client.ps = {}  # nothing running
        self.assertTrue(self._send_to_a_and_wait("Hello"))
        self.assertIn("not loaded", self._stats("A"))

    def test_stats_na_when_timing_absent(self):
        self.client.metrics = {"fake-a:latest": {}}  # no timing fields
        self.client.ps = {}
        self.assertTrue(self._send_to_a_and_wait("Hello"))
        self.assertEqual(
            self._stats("A"),
            "tok/s n/a · first token n/a · turn n/a · not loaded")

    def test_stats_update_after_relay_turns(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self.client.metrics = {
            "fake-a:latest": {"eval_count": 10,
                              "eval_duration_ns": 1000000000,
                              "ttft_s": 0.1, "total_s": 1.0},
            "fake-b:latest": {"eval_count": 30,
                              "eval_duration_ns": 1000000000,
                              "ttft_s": 0.2, "total_s": 1.5}}
        self.client.ps = {"fake-a:latest": 1900000000,
                          "fake-b:latest": 1500000000}
        self._start_relay("Talk about fruit.", cap=2)
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertEqual(
            self._stats("A"),
            "10.0 tok/s · first token 0.10s · turn 1.0s · 1.9 GB in RAM")
        self.assertEqual(
            self._stats("B"),
            "30.0 tok/s · first token 0.20s · turn 1.5s · 1.5 GB in RAM")

    def test_stats_reset_when_side_cleared(self):
        self.assertTrue(self._send_to_a_and_wait("Hello"))
        self.assertNotEqual(
            self._stats("A"),
            "-- tok/s · first token -- · turn -- · RAM --")
        self.state["widgets"]["A"]["clear"].click()
        self.assertEqual(
            self._stats("A"),
            "-- tok/s · first token -- · turn -- · RAM --")

    def test_summarize_replaces_older_turns(self):
        self.client.replies = {"fake-a:latest": [
            "alpha one", "alpha two", "alpha three",
            "They discussed fruit and picked apple pie."]}
        self.assertTrue(self._send_to_a_and_wait("first message"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        self.assertTrue(self._send_to_a_and_wait("third message"))
        convo = self.state["convo"]
        self.assertEqual(len(convo.sides["A"].history), 6)
        summarize_btn = self.state["widgets"]["A"]["summarize"]
        self.assertTrue(summarize_btn.isEnabled())
        summarize_btn.click()
        ok = self._wait_for(lambda: (
            len(convo.sides["A"].history) == 5
            and convo.sides["A"].labels[0] == SUMMARY_LABEL
            and not self.state["state"]["busy"]), timeout=15)
        self.assertTrue(ok, "history should fold into summary + last 4")
        self.assertEqual(
            [m["content"] for m in convo.sides["A"].history],
            ["Earlier in this conversation: "
             "They discussed fruit and picked apple pie.",
             "second message", "alpha two",
             "third message", "alpha three"])
        # The summary entry is visible in his window.
        self.assertIn("Summary of earlier messages", self._transcript("A"))
        self.assertIn("They discussed fruit", self._transcript("A"))
        # The summarize turn asked for a summary of the full history --
        # and the request itself never entered the history.
        model, summary_call = self.client.messages_seen[3]
        self.assertEqual(model, "fake-a:latest")
        self.assertEqual(len(summary_call), 7)
        self.assertEqual(summary_call[-1],
                         {"role": "user", "content": SUMMARIZE_PROMPT})
        # The next turn sends the summary + last 4, exactly.
        self.assertTrue(self._send_to_a_and_wait("fourth message"))
        self.assertEqual(self._last_messages_for("fake-a:latest"), [
            {"role": "user", "content":
             "Earlier in this conversation: "
             "They discussed fruit and picked apple pie."},
            {"role": "user", "content": "second message"},
            {"role": "assistant", "content": "alpha two"},
            {"role": "user", "content": "third message"},
            {"role": "assistant", "content": "alpha three"},
            {"role": "user", "content": "fourth message"},
        ])

    def test_summarize_button_disabled_with_short_history(self):
        btn = self.state["widgets"]["A"]["summarize"]
        self.assertFalse(btn.isEnabled())  # no history at all
        self.assertTrue(self._send_to_a_and_wait("hello"))
        self.assertFalse(btn.isEnabled())  # one exchange: nothing to fold

    def test_nudge_appears_past_budget_and_calls_nothing(self):
        side = self.state["convo"].sides["A"]
        self.assertFalse(self.state["widgets"]["A"]["nudge"].isVisible())
        side.add_user("x" * 13000)
        side.add_assistant("short")
        side.add_user("a")
        side.add_assistant("b")
        side.add_user("c")
        side.add_assistant("d")
        self.assertGreater(side.history_chars(), HISTORY_BUDGET_CHARS)
        self.state["refresh_buttons"]()
        self.assertTrue(self.state["widgets"]["A"]["nudge"].isVisible())
        self.assertIn("History is long",
                      self.state["widgets"]["A"]["nudge"].text())
        # The nudge never calls the model on its own.
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.client.messages_seen, [])

    def test_nudge_clears_after_summarize(self):
        side = self.state["convo"].sides["A"]
        side.add_user("x" * 13000)
        side.add_assistant("short")
        side.add_user("a")
        side.add_assistant("b")
        side.add_user("c")
        side.add_assistant("d")
        self.state["refresh_buttons"]()
        self.assertTrue(self.state["widgets"]["A"]["nudge"].isVisible())
        self.client.replies = {"fake-a:latest": ["A long ramble."]}
        self.state["widgets"]["A"]["summarize"].click()
        ok = self._wait_for(lambda: (
            len(side.history) == 5
            and not self.state["state"]["busy"]), timeout=15)
        self.assertTrue(ok, "summarize should fold the long history")
        self.assertLess(side.history_chars(), HISTORY_BUDGET_CHARS)
        self.assertFalse(self.state["widgets"]["A"]["nudge"].isVisible())

    def test_summarize_disabled_mid_generation(self):
        self._three_exchanges()
        btn = self.state["widgets"]["A"]["summarize"]
        self.assertTrue(btn.isEnabled())
        self.client.delay = 0.5
        self.client.replies = {"fake-a:latest": ["one two three four five"]}
        self.state["composer"].setPlainText("slow one")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["state"]["busy"], timeout=5))
        self.assertFalse(btn.isEnabled())
        self.assertFalse(
            self.state["widgets"]["B"]["summarize"].isEnabled())
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self.assertTrue(btn.isEnabled())

    def test_summarize_button_shows_working_state(self):
        self.client.replies = {"fake-a:latest": [
            "alpha one", "alpha two", "alpha three",
            " ".join(["word"] * 20)]}
        self.assertTrue(self._send_to_a_and_wait("first"))
        self.assertTrue(self._send_to_a_and_wait("second"))
        self.assertTrue(self._send_to_a_and_wait("third"))
        self.client.delay = 0.3
        btn = self.state["widgets"]["A"]["summarize"]
        self.assertTrue(btn.isEnabled())
        btn.click()
        self.assertTrue(self._wait_for(
            lambda: btn.text() == "Summarizing...", timeout=5))
        self.assertFalse(btn.isEnabled())
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self.assertEqual(btn.text(), "Summarize")
        self.assertTrue(btn.isEnabled())

    def test_summarize_disabled_mid_relay(self):
        self._three_exchanges()
        btn = self.state["widgets"]["A"]["summarize"]
        self.assertTrue(btn.isEnabled())
        self.client.replies = {"fake-a:latest": [" ".join(["word"] * 100)],
                               "fake-b:latest": ["short"]}
        self._start_relay("Go on.", cap=10)
        self.assertTrue(self._wait_for(
            lambda: self.state["state"].get("relay") is not None,
            timeout=5))
        self.assertFalse(btn.isEnabled())
        self.assertFalse(
            self.state["widgets"]["B"]["summarize"].isEnabled())
        self.state["relay_btn"].click()
        self.assertTrue(self._wait_for_relay_stopped())
        # The status flips to "stopped" before the cancelled job's
        # idle signal clears busy; wait for idle, then the button
        # must be back.
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=10))
        self.assertTrue(btn.isEnabled())


# ---------------------------------------------------------------------------
# Attachments (Item 5): pure logic, no Qt
# ---------------------------------------------------------------------------

class AttachmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, data):
        path = os.path.join(self.dir, name)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(path, mode) as fh:
            fh.write(data)
        return path

    def test_load_text_file_exact(self):
        path = self._write("notes.txt", "hello\nsecond line\n")
        self.assertEqual(load_attachment(path),
                         ("notes.txt", "hello\nsecond line\n"))

    def test_load_binary_rejected_with_plain_note(self):
        path = self._write("photo.png", b"\x89PNG\r\n\x1a\n\x00\xff\xfe")
        with self.assertRaises(AttachmentError) as ctx:
            load_attachment(path)
        self.assertIn("photo.png", str(ctx.exception))
        self.assertIn("isn't a text file", str(ctx.exception))

    def test_load_oversized_rejected_with_plain_note(self):
        path = self._write("big.txt", "x" * (MAX_ATTACHMENT_BYTES + 1))
        with self.assertRaises(AttachmentError) as ctx:
            load_attachment(path)
        self.assertIn("big.txt", str(ctx.exception))
        self.assertIn("over 100 KB", str(ctx.exception))

    def test_load_exactly_at_cap_is_fine(self):
        path = self._write("edge.txt", "x" * MAX_ATTACHMENT_BYTES)
        name, text = load_attachment(path)
        self.assertEqual(name, "edge.txt")
        self.assertEqual(len(text), MAX_ATTACHMENT_BYTES)

    def test_load_missing_file_plain_note(self):
        with self.assertRaises(AttachmentError) as ctx:
            load_attachment(os.path.join(self.dir, "gone.txt"))
        self.assertIn("gone.txt", str(ctx.exception))
        self.assertIn("Couldn't read", str(ctx.exception))

    def test_build_message_no_files_unchanged(self):
        self.assertEqual(build_attached_message([], "hello"), "hello")

    def test_build_message_one_file_exact(self):
        self.assertEqual(
            build_attached_message([("a.txt", "AAA")], "read this"),
            "File: a.txt\nAAA\n\nread this")

    def test_build_message_two_files_exact(self):
        self.assertEqual(
            build_attached_message(
                [("a.txt", "AAA"), ("b.md", "BBB")], "compare"),
            "File: a.txt\nAAA\n\nFile: b.md\nBBB\n\ncompare")

    def test_attachment_tag(self):
        self.assertEqual(attachment_tag(["a.txt"]), "(Attached: a.txt)")
        self.assertEqual(attachment_tag(["a.txt", "b.md"]),
                         "(Attached: a.txt, b.md)")

    def test_limits(self):
        self.assertEqual(MAX_ATTACHMENTS, 5)
        self.assertEqual(MAX_ATTACHMENT_BYTES, 100 * 1024)


class ChatSideDisplayTests(unittest.TestCase):
    def test_display_defaults_to_content(self):
        side = ChatSide("A")
        side.add_user("hello")
        side.add_assistant("hi")
        self.assertEqual(side.display_text(0), "hello")
        self.assertEqual(side.display_text(1), "hi")

    def test_display_override_shown_instead(self):
        side = ChatSide("A")
        side.add_user("File: a.txt\nAAA\n\nread this",
                      display="read this\n(Attached: a.txt)")
        self.assertEqual(side.display_text(0),
                         "read this\n(Attached: a.txt)")
        # The model still sees the full content.
        self.assertEqual(side.history[0]["content"],
                         "File: a.txt\nAAA\n\nread this")

    def test_display_moves_and_deletes_with_entries(self):
        side = ChatSide("A")
        side.add_user("one", display="ONE")
        side.add_user("two", display="TWO")
        side.move_up(1)
        self.assertEqual([side.display_text(i) for i in range(2)],
                         ["TWO", "ONE"])
        side.delete_at(0)
        self.assertEqual(side.display_text(0), "ONE")
        self.assertEqual(side.history[0]["content"], "one")

    def test_display_survives_summarize_for_kept_entries(self):
        side = ChatSide("A")
        for i in range(6):
            side.add_user("msg %d" % i, display="D%d" % i)
        self.assertTrue(side.summarize("a summary"))
        self.assertEqual(len(side.history), 5)
        # The summary entry itself has no override; the kept four do.
        self.assertEqual(side.display_text(0), side.history[0]["content"])
        self.assertEqual([side.display_text(i) for i in range(1, 5)],
                         ["D2", "D3", "D4", "D5"])

    def test_clear_clears_display_too(self):
        side = ChatSide("A")
        side.add_user("x", display="X")
        side.clear()
        self.assertEqual(side.display, [])


# ---------------------------------------------------------------------------
# Attachments (Item 5): GUI tests
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class Item5GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        self.client.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.pairs_file = os.path.join(self.tmp.name, "duo-pairs.json")
        self._env = mock.patch.dict(
            os.environ, {"DUAL_CHAT_PAIRS_FILE": self.pairs_file})
        self._env.start()
        self.window = build_window(self.client)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self._pick_models()

    def tearDown(self):
        self.window.close()
        self._env.stop()
        self.tmp.cleanup()

    def _wait_for(self, cond, timeout=10.0):
        from PySide6.QtTest import QTest
        deadline = time.time() + timeout
        while time.time() < deadline:
            QTest.qWait(50)
            if cond():
                return True
        return cond()

    def _pick_models(self):
        self.state["widgets"]["A"]["picker"].setCurrentIndex(0)
        self.state["widgets"]["B"]["picker"].setCurrentIndex(1)
        self.app.processEvents()

    def _write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        mode = "wb" if isinstance(data, bytes) else "w"
        with open(path, mode) as fh:
            fh.write(data)
        return path

    def _attach(self, *paths):
        return self.state["add_attachments"](list(paths))

    def _last_messages_for(self, model):
        for seen_model, messages in reversed(self.client.messages_seen):
            if seen_model == model:
                return messages
        return None

    def _send_both_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_both"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and len(self.state["convo"].sides["B"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def test_attach_list_shows_names_and_attaching_calls_nothing(self):
        a = self._write("notes.txt", "AAA")
        b = self._write("plan.md", "BBB")
        self.assertEqual(self._attach(a, b), 2)
        self.assertEqual([att["name"] for att in
                          self.state["state"]["attachments"]],
                         ["notes.txt", "plan.md"])
        self.assertTrue(self.state["attach_frame"].isVisible())
        from PySide6.QtWidgets import QLabel
        labels = [w.text() for w in
                  self.state["attach_frame"].findChildren(QLabel)]
        self.assertIn("notes.txt", labels)
        self.assertIn("plan.md", labels)
        # Attaching is free -- no model call.
        self.assertEqual(self.client.calls, [])

    def test_send_to_both_carries_exact_file_contents(self):
        a = self._write("notes.txt", "alpha contents")
        b = self._write("plan.md", "beta contents")
        self._attach(a, b)
        self.assertTrue(self._send_both_and_wait("read these"))
        expected = ("File: notes.txt\nalpha contents\n\n"
                    "File: plan.md\nbeta contents\n\nread these")
        for model in ("fake-a:latest", "fake-b:latest"):
            messages = self._last_messages_for(model)
            self.assertEqual(messages[-1]["role"], "user")
            self.assertEqual(messages[-1]["content"], expected)
        # The transcripts show the tag, not the file contents.
        for side in ("A", "B"):
            transcript = self.state["widgets"][side]["transcript"] \
                .toPlainText()
            self.assertIn("You: read these", transcript)
            self.assertIn("(Attached: notes.txt, plan.md)", transcript)
            self.assertNotIn("alpha contents", transcript)
            self.assertNotIn("beta contents", transcript)

    def test_binary_file_skipped_with_note(self):
        bad = self._write("photo.png", b"\x89PNG\r\n\x1a\n\x00\xff\xfe")
        self.assertEqual(self._attach(bad), 0)
        self.assertEqual(self.state["state"]["attachments"], [])
        message = self.window.statusBar().currentMessage()
        self.assertIn("photo.png", message)
        self.assertIn("isn't a text file", message)
        self.assertEqual(self.client.calls, [])

    def test_oversized_file_skipped_with_note(self):
        big = self._write("huge.txt", "x" * (MAX_ATTACHMENT_BYTES + 1))
        self.assertEqual(self._attach(big), 0)
        self.assertEqual(self.state["state"]["attachments"], [])
        message = self.window.statusBar().currentMessage()
        self.assertIn("huge.txt", message)
        self.assertIn("over 100 KB", message)

    def test_sixth_file_refused(self):
        paths = [self._write("f%d.txt" % i, "x") for i in range(6)]
        self.assertEqual(self._attach(*paths), 5)
        self.assertEqual(len(self.state["state"]["attachments"]), 5)
        message = self.window.statusBar().currentMessage()
        self.assertIn("up to 5 files", message)

    def test_attachments_clear_after_send(self):
        self._attach(self._write("notes.txt", "AAA"))
        self.assertTrue(self.state["attach_frame"].isVisible())
        self.assertTrue(self._send_both_and_wait("read this"))
        self.assertEqual(self.state["state"]["attachments"], [])
        self.assertFalse(self.state["attach_frame"].isVisible())

    def test_remove_button_drops_one_file(self):
        self._attach(self._write("a.txt", "AAA"),
                     self._write("b.txt", "BBB"))
        from PySide6.QtWidgets import QPushButton
        buttons = self.state["attach_frame"].findChildren(QPushButton)
        self.assertEqual(len(buttons), 2)
        buttons[0].click()
        self.assertEqual([att["name"] for att in
                          self.state["state"]["attachments"]], ["b.txt"])
        self.assertTrue(self.state["attach_frame"].isVisible())
        buttons = self.state["attach_frame"].findChildren(QPushButton)
        buttons[0].click()
        self.assertEqual(self.state["state"]["attachments"], [])
        self.assertFalse(self.state["attach_frame"].isVisible())

    def test_relay_carries_files_on_seed_only(self):
        self._attach(self._write("notes.txt", "seed file contents"))
        self.state["start_picker"].setCurrentIndex(0)
        self.state["cap_spin"].setValue(2)
        self.state["mode_picker"].setCurrentIndex(0)  # Blind
        self.state["composer"].setPlainText("discuss this")
        self.state["relay_btn"].click()
        ok = self._wait_for(lambda: (
            self.state["relay_status"].text().startswith("Relay stopped")
            and not self.state["state"]["busy"]), timeout=15)
        self.assertTrue(ok, "relay should run to the cap")
        seen = self.client.messages_seen
        self.assertEqual(len(seen), 2)
        seed_messages = seen[0][1]
        self.assertEqual(seed_messages[-1]["content"],
                         "File: notes.txt\nseed file contents\n\ndiscuss this")
        hop_messages = seen[1][1]
        self.assertNotIn("seed file contents",
                         json.dumps(hop_messages))
        # The seed's transcript shows the tag, not the contents.
        transcript = self.state["widgets"]["A"]["transcript"].toPlainText()
        self.assertIn("(Attached: notes.txt)", transcript)
        self.assertNotIn("seed file contents", transcript)

    def test_attach_disabled_mid_generation(self):
        btn = self.state["attach_btn"]
        self.assertTrue(btn.isEnabled())
        self.client.delay = 0.5
        self.client.replies = {"fake-a:latest": ["one two three four five"]}
        self.state["composer"].setPlainText("slow one")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["state"]["busy"], timeout=5))
        self.assertFalse(btn.isEnabled())
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self.assertTrue(btn.isEnabled())

    def test_render_transcript_keeps_short_display_after_reorder(self):
        self._attach(self._write("notes.txt", "secret contents here"))
        self.assertTrue(self._send_both_and_wait("read this"))
        self.state["render_transcript"]("A")
        transcript = self.state["widgets"]["A"]["transcript"].toPlainText()
        self.assertIn("(Attached: notes.txt)", transcript)
        self.assertNotIn("secret contents here", transcript)
        # And the history editor shows the short version too.
        self.state["refresh_history_list"]("A")
        lst = self.state["widgets"]["A"]["hist_list"]
        items = [lst.item(i).text() for i in range(lst.count())]
        self.assertTrue(any("(Attached: notes.txt)" in item
                            for item in items))
        self.assertFalse(any("secret contents here" in item
                             for item in items))
@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class Item6GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        self.client.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.pairs_file = os.path.join(self.tmp.name, "duo-pairs.json")
        self._env = mock.patch.dict(
            os.environ, {"DUAL_CHAT_PAIRS_FILE": self.pairs_file})
        self._env.start()
        self.window = build_window(self.client)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self._pick_models()

    def tearDown(self):
        self.window.close()
        self._env.stop()
        self.tmp.cleanup()

    def _wait_for(self, cond, timeout=10.0):
        from PySide6.QtTest import QTest
        deadline = time.time() + timeout
        while time.time() < deadline:
            QTest.qWait(50)
            if cond():
                return True
        return cond()

    def _pick_models(self):
        self.state["widgets"]["A"]["picker"].setCurrentIndex(0)
        self.state["widgets"]["B"]["picker"].setCurrentIndex(1)
        self.app.processEvents()

    def _wait_idle(self, timeout=15):
        return self._wait_for(
            lambda: not self.state["state"]["busy"]
            and self.state["state"]["task"] is None, timeout=timeout)

    def _run_task(self, worker_index, follow_up_index, text):
        self.state["task_edit"].setPlainText(text)
        self.state["task_worker"].setCurrentIndex(worker_index)
        self.state["task_follow_up"].setCurrentIndex(follow_up_index)
        self.app.processEvents()
        self.state["task_run"].click()

    def _last_messages_for(self, model):
        for seen_model, messages in reversed(self.client.messages_seen):
            if seen_model == model:
                return messages
        return None

    def test_run_task_disabled_until_all_picked(self):
        # Nothing pre-selected: both pickers blank, Run off.
        self.assertEqual(self.state["task_worker"].currentIndex(), -1)
        self.assertEqual(self.state["task_follow_up"].currentIndex(), -1)
        self.assertFalse(self.state["task_run"].isEnabled())
        # Task text alone is not enough.
        self.state["task_edit"].setPlainText("Write a draft.")
        self.app.processEvents()
        self.assertFalse(self.state["task_run"].isEnabled())
        # Worker alone is not enough.
        self.state["task_worker"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertFalse(self.state["task_run"].isEnabled())
        # All three set: Run enables.
        self.state["task_follow_up"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(self.state["task_run"].isEnabled())
        # Clearing the text disables it again.
        self.state["task_edit"].setPlainText("")
        self.app.processEvents()
        self.assertFalse(self.state["task_run"].isEnabled())

    def test_worker_receives_bare_task_text(self):
        self._run_task(0, 2, "Write a draft.")  # A works, Nothing follows
        self.assertTrue(self._wait_idle())
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        self.assertEqual(self.client.messages_seen,
                         [("fake-a:latest",
                           [{"role": "user", "content": "Write a draft."}])])
        # The task box cleared after the run started.
        self.assertEqual(self.state["task_edit"].toPlainText(), "")
        self.assertIn("Task done.",
                      self.window.statusBar().currentMessage())

    def test_nothing_makes_no_second_call_with_b_as_worker(self):
        self._run_task(1, 2, "Summarize this.")  # B works, Nothing follows
        self.assertTrue(self._wait_idle())
        self.assertEqual(self.client.calls, ["fake-b:latest"])
        self.assertEqual(self._last_messages_for("fake-b:latest"),
                         [{"role": "user", "content": "Summarize this."}])
        self.assertIsNone(self._last_messages_for("fake-a:latest"))

    def test_review_receives_exact_framed_result(self):
        self.client.replies = {"fake-a:latest": ["The draft is done."]}
        self._run_task(0, 0, "Write a draft.")  # A works, B reviews
        self.assertTrue(self._wait_idle())
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest"])
        self.assertEqual(self._last_messages_for("fake-a:latest"),
                         [{"role": "user", "content": "Write a draft."}])
        self.assertEqual(self._last_messages_for("fake-b:latest"),
                         [{"role": "user", "content": (
                             "Here is the result of a task. Review it and "
                             "say what is good, what is wrong, and what "
                             "you would change:\n\nThe draft is done.")}])
        # One at a time throughout.
        self.assertEqual(self.client.max_active, 1)

    def test_continue_receives_exact_framed_result(self):
        self.client.replies = {"fake-b:latest": ["Part one done."]}
        self._run_task(1, 1, "Start the story.")  # B works, A continues
        self.assertTrue(self._wait_idle())
        self.assertEqual(self.client.calls,
                         ["fake-b:latest", "fake-a:latest"])
        self.assertEqual(self._last_messages_for("fake-b:latest"),
                         [{"role": "user", "content": "Start the story."}])
        self.assertEqual(self._last_messages_for("fake-a:latest"),
                         [{"role": "user", "content": (
                             "Continue this work from where it left off:"
                             "\n\nPart one done.")}])

    def test_tags_in_window_only(self):
        self.client.replies = {"fake-a:latest": ["The draft is done."]}
        self._run_task(0, 0, "Write a draft.")
        self.assertTrue(self._wait_idle())
        a_text = self.state["widgets"]["A"]["transcript"].toPlainText()
        b_text = self.state["widgets"]["B"]["transcript"].toPlainText()
        self.assertIn("You [task]: Write a draft.", a_text)
        self.assertIn("You [task handoff]: "
                      "Task result from Model A, for review.", b_text)
        # The follow-up reply landed in B's pane as a normal reply.
        self.assertIn("fake-b:latest: Reply from fake-b:latest.", b_text)
        # No tag ever reached either model.
        self.assertNotIn("[task", json.dumps(self.client.messages_seen))
        self.assertNotIn("[task handoff]",
                         json.dumps(self.client.messages_seen))
        # The labels live on the history entries (window-only by design).
        self.assertEqual(self.state["convo"].sides["A"].labels[0],
                         "You [task]")
        self.assertEqual(self.state["convo"].sides["B"].labels[0],
                         "You [task handoff]")

    def test_run_disabled_mid_generation(self):
        self.state["task_edit"].setPlainText("Write a draft.")
        self.state["task_worker"].setCurrentIndex(0)
        self.state["task_follow_up"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(self.state["task_run"].isEnabled())
        self.client.delay = 0.5
        self.client.replies = {"fake-a:latest": ["one two three four five"]}
        self.state["composer"].setPlainText("slow one")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["state"]["busy"], timeout=5))
        self.assertFalse(self.state["task_run"].isEnabled())
        self.assertTrue(self._wait_idle())
        self.assertTrue(self.state["task_run"].isEnabled())

    def test_run_disabled_mid_relay(self):
        self.state["task_edit"].setPlainText("Write a draft.")
        self.state["task_worker"].setCurrentIndex(0)
        self.state["task_follow_up"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(self.state["task_run"].isEnabled())
        self.state["cap_spin"].setValue(2)
        self.state["composer"].setPlainText("talk")
        self.state["relay_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["state"]["busy"], timeout=5))
        self.assertFalse(self.state["task_run"].isEnabled())
        self.assertTrue(self._wait_for(
            lambda: self.state["relay_status"].text().startswith(
                "Relay stopped"), timeout=15))
        self.assertTrue(self._wait_idle())
        self.assertTrue(self.state["task_run"].isEnabled())

    def test_run_disabled_mid_task(self):
        self.client.delay = 0.5
        self.client.replies = {"fake-a:latest": ["one two three four five"]}
        self._run_task(0, 0, "Write a draft.")
        self.assertTrue(self._wait_for(
            lambda: self.state["state"]["busy"], timeout=5))
        # The setup can't change mid-task, and Run stays off.
        self.assertFalse(self.state["task_run"].isEnabled())
        self.assertFalse(self.state["task_edit"].isEnabled())
        self.assertFalse(self.state["task_worker"].isEnabled())
        self.assertFalse(self.state["task_follow_up"].isEnabled())
        self.assertTrue(self._wait_idle())
        self.assertTrue(self.state["task_edit"].isEnabled())

    def test_stop_mid_task_ends_task_cleanly(self):
        self.client.delay = 0.5
        self.client.replies = {"fake-a:latest": ["one two three four five"]}
        self._run_task(0, 0, "Write a draft.")
        self.assertTrue(self._wait_for(
            lambda: self.state["state"]["busy"], timeout=5))
        self.state["widgets"]["A"]["stop"].click()
        self.assertTrue(self._wait_for(
            lambda: "Task stopped." in
            self.window.statusBar().currentMessage(), timeout=10))
        self.assertTrue(self._wait_idle())
        self.assertIsNone(self.state["state"]["task"])
        # No second call: the task ended with the stop.
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        # And a normal send still works afterwards.
        self.client.delay = 0.02
        self.client.replies = {}
        self.state["composer"].setPlainText("still here")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(lambda: (
            "Reply from fake-a:latest." in
            self.state["widgets"]["A"]["transcript"].toPlainText()
            and not self.state["state"]["busy"]), timeout=15))

    def test_run_task_needs_a_model(self):
        # Unpick both models: Run is still offered (text + pickers are
        # set), but starting reports the missing model plainly.
        self.state["widgets"]["A"]["picker"].setCurrentIndex(-1)
        self.state["widgets"]["B"]["picker"].setCurrentIndex(-1)
        self.app.processEvents()
        self.state["task_edit"].setPlainText("Write a draft.")
        self.state["task_worker"].setCurrentIndex(0)
        self.state["task_follow_up"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(self.state["task_run"].isEnabled())
        self.state["task_run"].click()
        self.assertIn("Pick a model for Model A first.",
                      self.window.statusBar().currentMessage())
        self.assertEqual(self.client.calls, [])
        self.assertIsNone(self.state["state"]["task"])

    def test_attachments_do_not_ride_on_tasks(self):
        path = os.path.join(self.tmp.name, "notes.txt")
        with open(path, "w") as fh:
            fh.write("secret file contents")
        self.assertEqual(self.state["add_attachments"]([path]), 1)
        self.client.replies = {"fake-a:latest": ["The draft is done."]}
        self._run_task(0, 0, "Write a draft.")
        self.assertTrue(self._wait_idle())
        self.assertNotIn("secret file contents",
                         json.dumps(self.client.messages_seen))
        # The attachment is still there for the next normal send.
        self.assertEqual([att["name"] for att in
                          self.state["state"]["attachments"]],
                         ["notes.txt"])


if __name__ == "__main__":
    unittest.main()
