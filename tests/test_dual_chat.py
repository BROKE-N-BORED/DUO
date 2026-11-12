"""Tests for DUO Items 1+2. No real Ollama needed: a fake Ollama HTTP
server (and a fake client for the GUI tests) stands in for the real one.

Run from the project root:  python3 -m unittest discover -s tests -v
"""
import copy
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dual_chat import (  # noqa: E402
    AGREEMENT_PHRASES, AWARE_TEMPLATE, Cancelled, ChatSide, Conversation,
    NoModelSelected, OllamaClient, OllamaError, RelaySession, SerialRunner,
    parse_ollama_list_output, parse_stop_phrases, parse_tags_payload,
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
                            "done": True, "done_reason": "stop"}
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


# ---------------------------------------------------------------------------
# GUI tests (offscreen)
# ---------------------------------------------------------------------------

class FakeGuiClient:
    """Stands in for OllamaClient in the GUI tests.

    ``replies`` maps a model to a scripted list of replies, consumed in
    order (the last one repeats). ``messages_seen`` records every messages
    list the app sent, deep-copied, so relay tests can assert the exact
    text each model received. ``max_active`` proves generations never
    overlap. Call reset() between tests.
    """

    def __init__(self):
        self.calls = []
        self.messages_seen = []
        self.replies = {}
        self.reply_index = {}
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
            self.max_active = 0
            self._active = 0
            self.delay = 0.02

    def list_models(self, timeout=5):
        return ["fake-a:latest", "fake-b:latest"]

    def chat_stream(self, model, messages, on_chunk, cancel_event, timeout=300):
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


if __name__ == "__main__":
    unittest.main()
