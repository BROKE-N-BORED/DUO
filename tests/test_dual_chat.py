"""Tests for DUO. No real Ollama needed: a fake Ollama HTTP
server (and a fake client for the GUI tests) stands in for the real one.

Run from the project root:  python3 -m unittest discover -s tests -v
"""
import copy
import datetime
import inspect
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dual_chat import (  # noqa: E402
    AGREEMENT_PHRASES, APP_VERSION, AWARE_TEMPLATE, CHAT_MAX_ATTEMPTS,
    CHAT_RETRY_DELAYS, CHAT_TIMEOUT, COMPARE_PREFIX,
    CONVERSATION_FORMAT_VERSION, DECK_PAGE_HTML,
    DECK_PORT, MAX_ATTACHMENTS,
    MAX_ATTACHMENT_BYTES, MSG_EMPTY, MSG_MODEL_FAILED, MSG_OLLAMA_DOWN,
    MSG_OLLAMA_NOT_INSTALLED, MSG_OLLAMA_SLOW, MSG_OLLAMA_START_FAILED,
    MSG_PHONE_BUSY, MSG_PHONE_RELAY, MSG_PHONE_TARGET,
    SETTINGS_APP, SETTINGS_ORG, SETTING_ENTER_TO_SEND,
    SETTING_SPLITTER_STATE, SETTING_TEMPERATURE,
    SETTING_WINDOW_GEOMETRY,
    TASK_CONTINUE_TEMPLATE, TASK_FOLLOW_UPS, TASK_REVIEW_TEMPLATE,
    TEMPERATURE_DEFAULT, TEMPERATURE_MAX, TEMPERATURE_MIN,
    AttachmentError, Cancelled,
    ChatSide, Conversation, ConversationError, DeckServer,
    HISTORY_BUDGET_CHARS,
    NoModelSelected,
    OllamaClient, OllamaError, OllamaServerManager, RelaySession,
    SUMMARY_KEEP_LAST, SUMMARY_LABEL, SUMMARIZE_PROMPT, SerialRunner,
    TaskSession, attachment_tag, build_attached_message,
    build_deck_state, compute_turn_stats, conversation_to_data,
    data_to_conversation, default_both_filename,
    default_conversation_filename, default_debug_filename,
    default_full_filename, default_side_filename, delete_pair,
    format_both_transcript,
    format_full_transcript, format_ram, format_side_transcript,
    format_stats_line, get_lan_ip, load_attachment,
    load_conversation_file, load_pairs,
    make_debug_logger,
    pairs_path, parse_ollama_list_output, parse_ps_payload,
    parse_stop_phrases, parse_tags_payload, phrase_matches,
    render_qr_pixmap,
    sanitize_filename_part,
    save_conversation_file, save_pair,
    write_text_atomic,
)

# One shared debug logger for the GUI tests, writing to a temp dir so
# the test runs never leave a duo-debug.log in the project tree. (One
# instance, shared: a DebugLogger per window would stack duplicate
# handlers onto the same named logger.)
_TEST_LOG_DIR = tempfile.mkdtemp(prefix="duo-test-logs-")
TEST_LOGGER = make_debug_logger(log_dir=_TEST_LOG_DIR)


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
    # Every /api/chat request body received, in order. Tests that
    # assert on the wire (e.g. the options tests) clear it first.
    bodies = []
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
        FakeOllamaHandler.bodies.append(payload)
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

    def test_stop_phrase_case_insensitive_whole_word(self):
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

    # v0.12: whole-word matching. Substring matching ended a relay the
    # moment a model DISAGREED ("I disagreed" contains "agreed").

    def test_phrase_matches_whole_words(self):
        self.assertTrue(phrase_matches("agreed", "Agreed, moving on."))
        self.assertTrue(phrase_matches("agreed", "agreed"))
        self.assertTrue(phrase_matches("i agree", "I AGREE with you."))
        self.assertTrue(phrase_matches("sounds good", "Sounds good!"))
        self.assertFalse(phrase_matches("agreed", "I disagreed with that"))
        self.assertFalse(phrase_matches("i agree", "disagreement"))
        self.assertFalse(phrase_matches("cap", "the capital city"))
        self.assertFalse(phrase_matches("", "anything at all"))

    def test_agreement_ignores_disagreed(self):
        session = RelaySession("A", stop_on_agreement=True)
        self.assertIsNone(session.find_agreement("I disagreed with that"))
        self.assertIsNone(session.find_agreement(
            "There is disagreement here."))
        self.assertIsNone(session.find_agreement(
            "I disagree with that idea."))

    def test_agreement_whole_words_still_match(self):
        session = RelaySession("A", stop_on_agreement=True)
        self.assertEqual(session.find_agreement("I agree with you."),
                         "i agree")
        self.assertEqual(session.find_agreement("agreed"), "agreed")
        self.assertEqual(session.find_agreement("Agreed, moving on."),
                         "agreed")
        self.assertEqual(session.find_agreement("That works for me."),
                         "that works")

    def test_stop_phrase_whole_words(self):
        session = RelaySession("A", stop_phrases=["cap"])
        self.assertIsNone(session.find_stop_phrase("the capital city"))
        self.assertIsNone(session.find_stop_phrase("recap the meeting"))
        self.assertEqual(session.find_stop_phrase("hit the cap."),
                         "cap")
        self.assertEqual(session.find_stop_phrase("Cap off the day."),
                         "cap")

    def test_check_stop_agreement_ignores_disagreed(self):
        session = RelaySession("A", turn_cap=10, stop_on_agreement=True)
        session.turns_done = 1
        self.assertIsNone(session.check_stop("I disagreed with that"))
        self.assertEqual(session.check_stop("I agree with you."),
                         "agreement reached ('i agree')")

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

    def test_chat_stream_sends_options(self):
        FakeOllamaHandler.bodies.clear()
        client = OllamaClient(self.url)
        full = client.chat_stream(
            "fake-a:latest", [{"role": "user", "content": "hi"}],
            lambda t: None, threading.Event(),
            options={"temperature": 1.4})
        self.assertEqual(full, "Hello from A.")
        self.assertEqual(len(FakeOllamaHandler.bodies), 1)
        self.assertEqual(FakeOllamaHandler.bodies[0].get("options"),
                         {"temperature": 1.4})

    def test_chat_stream_without_options_sends_no_options_key(self):
        FakeOllamaHandler.bodies.clear()
        client = OllamaClient(self.url)
        client.chat_stream(
            "fake-a:latest", [{"role": "user", "content": "hi"}],
            lambda t: None, threading.Event())
        self.assertEqual(len(FakeOllamaHandler.bodies), 1)
        self.assertNotIn("options", FakeOllamaHandler.bodies[0])

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
    text each model received. ``options_seen`` records the options dict
    passed with each turn, so temperature tests can assert what each
    side transmitted. ``max_active`` proves generations never
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
        self.options_seen = []
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
            self.options_seen.clear()
            self.replies.clear()
            self.reply_index.clear()
            self.metrics.clear()
            self.ps.clear()
            self.max_active = 0
            self._active = 0
            self.delay = 0.02

    def list_models(self, timeout=5):
        return ["fake-a:latest", "fake-b:latest"]

    def probe(self, timeout=5):
        return True

    def running_models(self, timeout=5):
        return dict(self.ps)

    def chat_stream(self, model, messages, on_chunk, cancel_event,
                    timeout=1800, on_metrics=None, on_retry=None,
                    options=None):
        with self.lock:
            self.calls.append(model)
            self.messages_seen.append((model, copy.deepcopy(messages)))
            self.options_seen.append((model, options))
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
        self.window = build_window(self.client, logger=TEST_LOGGER)
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
        window = build_window(self.client, logger=TEST_LOGGER)
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

        window = build_window(DownClient(), logger=TEST_LOGGER)
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
        self.window = build_window(self.client, logger=TEST_LOGGER)
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
        window = build_window(self.client, logger=TEST_LOGGER)
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

    def test_close_window_mid_relay_stops_the_relay(self):
        # Regression: closing mid-relay used to leave the relay
        # running in the closed window, still calling the model (and,
        # in the suite, still appending to the shared fake client's
        # call list long after the window was gone -- which broke
        # later tests' exact call counts). Closing must stop the work.
        self.client.replies = {"fake-a:latest": ["a one", "a two"],
                               "fake-b:latest": ["b one", "b two"]}
        self._start_relay("Go on and on.", cap=50)
        self.assertTrue(self._wait_for(
            lambda: len(self.client.calls) >= 1), "first turn started")
        self.window.close()
        calls_at_close = list(self.client.calls)
        # Pump the loop so queued signals land, then give the relay
        # time it would have used to place more calls (a turn takes
        # ~0.1s with the fake client).
        self._wait_for(lambda: False, timeout=2.0)
        self.assertEqual(list(self.client.calls), calls_at_close)

    def test_close_window_mid_relay_is_quiet(self):
        # Closing while a relay runs must not crash or raise: queued
        # runner signals landing after the window is gone are ignored.
        from dual_chat import build_window
        window = build_window(self.client, logger=TEST_LOGGER)
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

    def test_delete_pair_removes_only_that_pair(self):
        save_pair(self.path, "one", {"model_a": "a", "model_b": None,
                                     "instruction_a": "", "instruction_b": ""})
        save_pair(self.path, "two", {"model_a": "b", "model_b": "c",
                                     "instruction_a": "x", "instruction_b": "y"})
        self.assertTrue(delete_pair(self.path, "one"))
        pairs = load_pairs(self.path)
        self.assertEqual(set(pairs), {"two"})
        self.assertEqual(pairs["two"]["model_a"], "b")

    def test_delete_pair_missing_name_is_noop(self):
        save_pair(self.path, "one", {"model_a": "a", "model_b": None,
                                     "instruction_a": "", "instruction_b": ""})
        self.assertFalse(delete_pair(self.path, "nope"))
        self.assertEqual(set(load_pairs(self.path)), {"one"})
        self.assertFalse(delete_pair(self.path, "nope"))

    def test_pairs_path_env_override(self):
        with mock.patch.dict(os.environ,
                             {"DUAL_CHAT_PAIRS_FILE": "/tmp/elsewhere.json"}):
            self.assertEqual(pairs_path(), "/tmp/elsewhere.json")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(pairs_path().endswith("duo-pairs.json"))

    def test_save_leaves_no_temp_behind(self):
        save_pair(self.path, "one", {"model_a": "a", "model_b": None,
                                     "instruction_a": "", "instruction_b": ""})
        leftovers = [n for n in os.listdir(self.tmp.name)
                     if n.startswith(".duo-pairs-")]
        self.assertEqual(leftovers, [])
        self.assertEqual(set(os.listdir(self.tmp.name)),
                         {"duo-pairs.json"})

    def test_save_failure_leaves_old_file_intact(self):
        setup = {"model_a": "a", "model_b": None,
                 "instruction_a": "", "instruction_b": ""}
        save_pair(self.path, "one", setup)
        with open(self.path, "rb") as fh:
            before = fh.read()
        # The rename is the step that fails: the new content is
        # written to a temp file, then the swap itself blows up.
        with mock.patch("dual_chat.os.replace",
                        side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_pair(self.path, "two", setup)
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(load_pairs(self.path), {"one": setup})
        leftovers = [n for n in os.listdir(self.tmp.name)
                     if n.startswith(".duo-pairs-")]
        self.assertEqual(leftovers, [])

    def test_delete_failure_leaves_old_file_intact(self):
        save_pair(self.path, "one", {"model_a": "a", "model_b": None,
                                     "instruction_a": "", "instruction_b": ""})
        save_pair(self.path, "two", {"model_a": "b", "model_b": None,
                                     "instruction_a": "", "instruction_b": ""})
        with open(self.path, "rb") as fh:
            before = fh.read()
        with mock.patch("dual_chat.os.replace",
                        side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                delete_pair(self.path, "one")
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(set(load_pairs(self.path)), {"one", "two"})
        leftovers = [n for n in os.listdir(self.tmp.name)
                     if n.startswith(".duo-pairs-")]
        self.assertEqual(leftovers, [])


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
        self.window = build_window(self.client, logger=TEST_LOGGER)
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
        window = build_window(self.client, logger=TEST_LOGGER)
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

    def test_pair_save_failure_shows_plain_note(self):
        # v0.18 (audit T7): a failed pairs save says so plainly
        # instead of crashing, and the old pairs file stays intact.
        save_pair(self.pairs_file, "old pair",
                  {"model_a": "fake-a:latest", "model_b": None,
                   "instruction_a": "", "instruction_b": ""})
        with open(self.pairs_file, "rb") as fh:
            before = fh.read()
        self.state["pair_name"].setText("new pair")
        with mock.patch("dual_chat.os.replace",
                        side_effect=OSError("disk full")):
            self.state["pair_save"].click()
        self.app.processEvents()
        message = self.window.statusBar().currentMessage()
        self.assertTrue(
            message.startswith("Couldn't save pair 'new pair'"),
            message)
        self.assertIn("disk full", message)
        with open(self.pairs_file, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(set(load_pairs(self.pairs_file)), {"old pair"})
        # The failed save never refreshed the picker: the new pair
        # is not listed.
        self.assertEqual(self.state["pair_combo"].count(), 0)
        leftovers = [n for n in os.listdir(self.tmp.name)
                     if n.startswith(".duo-pairs-")]
        self.assertEqual(leftovers, [])

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
        self.window = build_window(self.client, logger=TEST_LOGGER)
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

    def test_add_user_and_add_assistant_return_their_labels(self):
        side = ChatSide("A")
        side.model = "fake-a:latest"
        self.assertEqual(side.add_user("hello"), "You")
        self.assertEqual(side.add_user("x", label="You [task]"),
                         "You [task]")
        self.assertEqual(side.add_assistant("hi"), "fake-a:latest")
        self.assertEqual(side.add_assistant("hi", label="Custom"),
                         "Custom")


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
        self.window = build_window(self.client, logger=TEST_LOGGER)
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

    def test_status_line_clears_stale_attach_note_after_send(self):
        # Regression: the "Attached: ..." status note used to linger
        # after the send even though the attachments had cleared.
        self._attach(self._write("notes.txt", "AAA"))
        self.assertIn("Attached: notes.txt.",
                      self.window.statusBar().currentMessage())
        self.state["composer"].setPlainText("read this")
        self.state["send_both"].click()
        # Assert right after the click, before the 3-second note
        # timeout could expire and hide the bug.
        self.app.processEvents()
        self.assertNotIn("Attached:",
                         self.window.statusBar().currentMessage())
        # Let the turn finish so the window closes cleanly.
        self.assertTrue(self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15))

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
        self.window = build_window(self.client, logger=TEST_LOGGER)
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


# ---------------------------------------------------------------------------
# Transcripts (Item 7): pure logic, no Qt
# ---------------------------------------------------------------------------

class TranscriptFormatTests(unittest.TestCase):
    WHEN = datetime.datetime(2026, 11, 12, 19, 30)
    DAY = datetime.date(2026, 11, 12)

    def _side(self, name="A", model="fake-a:latest"):
        side = ChatSide(name)
        side.model = model
        return side

    def test_side_export_exact(self):
        side = self._side()
        side.add_user("hello")
        side.add_assistant("hi there")
        self.assertEqual(
            format_side_transcript(side, self.WHEN),
            "DUO transcript -- Model A (fake-a:latest)"
            " -- saved 2026-11-12 19:30\n\n"
            "You: hello\n\n"
            "fake-a:latest: hi there\n")

    def test_labels_included(self):
        side = self._side()
        side.add_user("rain is lovely", label="You [relay from Model B]")
        side.add_assistant("I agree")
        side.add_user("count the stars", label="You [task]")
        side.add_assistant("counted them")
        side.add_user("Here is the result of a task. Review it and say "
                      "what is good, what is wrong, and what you would "
                      "change:\n\ncounted them",
                      label="You [task handoff]",
                      display="Task result from Model B, for review.")
        side.add_assistant("looks good")
        text = format_side_transcript(side, self.WHEN)
        self.assertIn("You [relay from Model B]: rain is lovely", text)
        self.assertIn("You [task]: count the stars", text)
        self.assertIn("You [task handoff]: "
                      "Task result from Model B, for review.", text)
        # The handoff shows the short note, not the full framed text.
        self.assertNotIn("what is good, what is wrong", text)

    def test_summary_entry_included(self):
        side = self._side()
        for i in range(5):
            side.add_user("user %d" % i)
        self.assertTrue(side.summarize("we talked about rain"))
        text = format_side_transcript(side, self.WHEN)
        self.assertIn("Summary of earlier messages: "
                      "Earlier in this conversation: "
                      "we talked about rain", text)
        # The folded message is gone; the last four stay.
        self.assertNotIn("user 0", text)
        self.assertIn("You: user 4", text)

    def test_file_contents_never_pasted_in(self):
        side = self._side()
        full = build_attached_message(
            [("notes.txt", "SECRET FILE CONTENTS")], "please read this")
        side.add_user(full, display="please read this\n"
                                    "(Attached: notes.txt)")
        text = format_side_transcript(side, self.WHEN)
        self.assertNotIn("SECRET FILE CONTENTS", text)
        self.assertNotIn("File: notes.txt", text)
        self.assertIn("You: please read this\n(Attached: notes.txt)", text)

    def test_empty_transcript(self):
        self.assertEqual(
            format_side_transcript(self._side(), self.WHEN),
            "DUO transcript -- Model A (fake-a:latest)"
            " -- saved 2026-11-12 19:30\n\n"
            "No messages yet.\n")

    def test_no_model_named_in_header(self):
        side = ChatSide("B")
        text = format_side_transcript(side, self.WHEN)
        self.assertTrue(
            text.startswith("DUO transcript -- Model B (no model picked)"
                            " -- saved 2026-11-12 19:30\n\n"))

    def test_both_sections_in_order(self):
        convo = Conversation()
        convo.sides["A"].model = "fake-a:latest"
        convo.sides["B"].model = "fake-b:latest"
        convo.sides["A"].add_user("a one")
        convo.sides["A"].add_assistant("a two")
        convo.sides["B"].add_user("b one")
        self.assertEqual(
            format_both_transcript(convo, self.WHEN),
            "DUO transcript -- saved 2026-11-12 19:30\n\n"
            "Model A (fake-a:latest)\n\n"
            "You: a one\n\n"
            "fake-a:latest: a two\n\n"
            "Model B (fake-b:latest)\n\n"
            "You: b one\n")

    def test_both_empty_side(self):
        convo = Conversation()
        convo.sides["A"].model = "fake-a:latest"
        convo.sides["B"].model = "fake-b:latest"
        convo.sides["B"].add_user("b one")
        self.assertEqual(
            format_both_transcript(convo, self.WHEN),
            "DUO transcript -- saved 2026-11-12 19:30\n\n"
            "Model A (fake-a:latest)\n\n"
            "No messages yet.\n\n"
            "Model B (fake-b:latest)\n\n"
            "You: b one\n")

    def test_side_filename_sanitizes_model(self):
        self.assertEqual(
            default_side_filename("A", "qwen3.5:2b", self.DAY),
            "duo-model-a-qwen3.5-2b-2026-11-12.txt")
        self.assertEqual(
            default_side_filename("B", "fake-b:latest", self.DAY),
            "duo-model-b-fake-b-latest-2026-11-12.txt")

    def test_side_filename_without_model(self):
        self.assertEqual(
            default_side_filename("A", None, self.DAY),
            "duo-model-a-2026-11-12.txt")

    def test_both_filename(self):
        self.assertEqual(default_both_filename(self.DAY),
                         "duo-both-2026-11-12.txt")

    def test_sanitize_filename_part(self):
        self.assertEqual(sanitize_filename_part("a/b\\c:d*e"), "a-b-c-d-e")
        self.assertEqual(sanitize_filename_part("qwen3.5:2b"), "qwen3.5-2b")
        self.assertEqual(sanitize_filename_part("plain-name_1"), "plain-name_1")


class FullTranscriptFormatTests(unittest.TestCase):
    """The full transcript (Item 9): both sides interleaved in the
    order the conversation actually happened."""
    WHEN = datetime.datetime(2026, 11, 12, 20, 5)
    DAY = datetime.date(2026, 11, 12)

    def test_full_interleaves_both_sides_exact(self):
        log = [("A", "You", "which is better?"),
               ("B", "You", "which is better?"),
               ("A", "fake-a:latest", "alpha one"),
               ("B", "fake-b:latest", "beta one"),
               ("A", "You", "Talk to each other."),
               ("A", "fake-a:latest", "alpha two"),
               ("B", "You [relay from Model A]", "alpha two"),
               ("B", "fake-b:latest", "beta two"),
               ("A", "You [task]", "Count the stars."),
               ("A", "fake-a:latest", "alpha three"),
               ("B", "You [task handoff]",
                "Task result from Model A, for review."),
               ("B", "fake-b:latest", "beta three")]
        self.assertEqual(
            format_full_transcript(log, self.WHEN),
            "DUO transcript -- saved 2026-11-12 20:05\n\n"
            "A -- You: which is better?\n\n"
            "B -- You: which is better?\n\n"
            "A -- fake-a:latest: alpha one\n\n"
            "B -- fake-b:latest: beta one\n\n"
            "A -- You: Talk to each other.\n\n"
            "A -- fake-a:latest: alpha two\n\n"
            "B -- You [relay from Model A]: alpha two\n\n"
            "B -- fake-b:latest: beta two\n\n"
            "A -- You [task]: Count the stars.\n\n"
            "A -- fake-a:latest: alpha three\n\n"
            "B -- You [task handoff]: "
            "Task result from Model A, for review.\n\n"
            "B -- fake-b:latest: beta three\n")

    def test_full_side_prefixes(self):
        log = [("A", "You", "hi"),
               ("B", "lfm3:1.7b", "hello back")]
        text = format_full_transcript(log, self.WHEN)
        self.assertIn("A -- You: hi", text)
        self.assertIn("B -- lfm3:1.7b: hello back", text)

    def test_full_empty_log(self):
        self.assertEqual(
            format_full_transcript([], self.WHEN),
            "DUO transcript -- saved 2026-11-12 20:05\n\n"
            "No messages yet.\n")

    def test_full_filename(self):
        self.assertEqual(default_full_filename(self.DAY),
                         "duo-full-2026-11-12.txt")


# ---------------------------------------------------------------------------
# Transcripts (Item 7): GUI
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class Item7GuiTests(unittest.TestCase):
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
        self.window = build_window(self.client, logger=TEST_LOGGER)
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

    def _send_to_a_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_a"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _send_both_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_both"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and len(self.state["convo"].sides["B"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15)

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

    def _save_dialog(self, path):
        """Patch the save dialog to return ``path`` ("" cancels)."""
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getSaveFileName",
            return_value=(path, "Text files (*.txt)"))

    def _read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_save_side_writes_exact_file(self):
        self.assertTrue(self._send_to_a_and_wait("hello"))
        path = os.path.join(self.tmp.name, "out.txt")
        with self._save_dialog(path):
            self.state["widgets"]["A"]["save"].click()
        text = self._read(path)
        lines = text.split("\n")
        self.assertRegex(
            lines[0],
            r"^DUO transcript -- Model A \(fake-a:latest\)"
            r" -- saved \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(
            "\n".join(lines[1:]),
            "\nYou: hello\n\n"
            "fake-a:latest: Reply from fake-a:latest.\n")
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Saved to %s" % path)

    def test_save_both_writes_both_sections(self):
        self.assertTrue(self._send_both_and_wait("which is better?"))
        path = os.path.join(self.tmp.name, "both.txt")
        with self._save_dialog(path):
            self.state["save_both"].click()
        text = self._read(path)
        self.assertRegex(
            text.split("\n")[0],
            r"^DUO transcript -- saved \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        a_at = text.index("Model A (fake-a:latest)")
        b_at = text.index("Model B (fake-b:latest)")
        self.assertLess(a_at, b_at)
        self.assertIn("You: which is better?", text)
        self.assertIn("fake-a:latest: Reply from fake-a:latest.", text)
        self.assertIn("fake-b:latest: Reply from fake-b:latest.", text)
        self.assertEqual(text.count("which is better?"), 2)

    def test_save_empty_transcript(self):
        path = os.path.join(self.tmp.name, "empty.txt")
        with self._save_dialog(path):
            self.state["widgets"]["A"]["save"].click()
        text = self._read(path)
        lines = text.split("\n")
        self.assertRegex(
            lines[0],
            r"^DUO transcript -- Model A \(fake-a:latest\)"
            r" -- saved \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual("\n".join(lines[1:]), "\nNo messages yet.\n")

    def test_cancel_writes_nothing(self):
        self.assertTrue(self._send_to_a_and_wait("hello"))
        before = set(os.listdir(self.tmp.name))
        with self._save_dialog(""):
            self.state["widgets"]["A"]["save"].click()
        self.assertEqual(set(os.listdir(self.tmp.name)), before)

    def test_write_failure_shows_plain_note(self):
        self.assertTrue(self._send_to_a_and_wait("hello"))
        bad = os.path.join(self.tmp.name, "no-such-dir", "out.txt")
        with self._save_dialog(bad):
            self.state["widgets"]["A"]["save"].click()
        message = self.window.statusBar().currentMessage()
        self.assertTrue(message.startswith("Couldn't save to %s" % bad),
                        message)
        self.assertIn("No such file or directory", message)
        # No crash: the window is still alive and the transcript intact.
        self.assertTrue(self.window.isVisible())
        self.assertIn("hello", self.state["convo"].sides["A"].history[0]
                      ["content"])

    def test_failed_overwrite_leaves_old_file_intact(self):
        # v0.18 (audit T7): the save is atomic, so a failure at the
        # rename step leaves the file that was already there
        # byte-identical, with a plain note and no temp left behind.
        self.assertTrue(self._send_to_a_and_wait("hello"))
        path = os.path.join(self.tmp.name, "out.txt")
        with self._save_dialog(path):
            self.state["widgets"]["A"]["save"].click()
        with open(path, "rb") as fh:
            before = fh.read()
        with mock.patch("dual_chat.os.replace",
                        side_effect=OSError("disk full")), \
                self._save_dialog(path):
            self.state["widgets"]["A"]["save"].click()
        message = self.window.statusBar().currentMessage()
        self.assertTrue(message.startswith("Couldn't save to %s" % path),
                        message)
        self.assertIn("disk full", message)
        with open(path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        leftovers = [n for n in os.listdir(self.tmp.name)
                     if n.startswith(".duo-")]
        self.assertEqual(leftovers, [])

    def test_save_disabled_mid_generation(self):
        save_a = self.state["widgets"]["A"]["save"]
        save_b = self.state["widgets"]["B"]["save"]
        self.assertTrue(save_a.isEnabled())
        self.assertTrue(self.state["save_both"].isEnabled())
        self.client.delay = 0.5
        self.client.replies = {"fake-a:latest": ["one two three four five"]}
        self.state["composer"].setPlainText("slow one")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["state"]["busy"], timeout=5))
        self.assertFalse(save_a.isEnabled())
        self.assertFalse(save_b.isEnabled())
        self.assertFalse(self.state["save_both"].isEnabled())
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self.assertTrue(save_a.isEnabled())
        self.assertTrue(self.state["save_both"].isEnabled())

    def test_save_disabled_mid_relay(self):
        save_a = self.state["widgets"]["A"]["save"]
        self.assertTrue(save_a.isEnabled())
        self.client.replies = {"fake-a:latest": [" ".join(["word"] * 100)],
                               "fake-b:latest": ["short"]}
        self._start_relay("Go on.", cap=10)
        self.assertTrue(self._wait_for(
            lambda: self.state["state"].get("relay") is not None,
            timeout=5))
        self.assertFalse(save_a.isEnabled())
        self.assertFalse(
            self.state["widgets"]["B"]["save"].isEnabled())
        self.assertFalse(self.state["save_both"].isEnabled())
        self.state["relay_btn"].click()
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=10))
        self.assertTrue(save_a.isEnabled())
        self.assertTrue(self.state["save_both"].isEnabled())


# ---------------------------------------------------------------------------
# Chat layout + full transcript (Item 9): GUI tests
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class Item9GuiTests(unittest.TestCase):
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
        self.window = build_window(self.client, logger=TEST_LOGGER)
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

    def _fragments(self, side):
        """[(text, is_bold)] for every fragment in a pane's document,
        in order -- the rendered format, not just the plain string."""
        from PySide6 import QtGui
        doc = self.state["widgets"][side]["transcript"].document()
        out = []
        block = doc.firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    out.append((frag.text(),
                                frag.charFormat().fontWeight()
                                >= QtGui.QFont.Weight.Bold))
                it += 1
            block = block.next()
        return out

    def _send_to_a_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_a"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _send_both_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_both"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and len(self.state["convo"].sides["B"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15)

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

    def _open_history(self, side):
        w = self.state["widgets"][side]
        if not w["hist_toggle"].isChecked():
            w["hist_toggle"].click()
        self.app.processEvents()
        self.assertTrue(w["hist_frame"].isVisible())

    def _save_dialog(self, path):
        """Patch the save dialog to return ``path`` ("" cancels)."""
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getSaveFileName",
            return_value=(path, "Text files (*.txt)"))

    def _read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_wrap_mode_is_widget_width(self):
        from PySide6 import QtWidgets
        for side in ("A", "B"):
            self.assertEqual(
                self.state["widgets"][side]["transcript"].lineWrapMode(),
                QtWidgets.QTextEdit.LineWrapMode.WidgetWidth)

    def test_blank_line_between_messages_and_bold_labels(self):
        self.assertTrue(self._send_to_a_and_wait("hello"))
        text = self._transcript("A")
        self.assertIn(
            "You: hello\n\nfake-a:latest: Reply from fake-a:latest.",
            text)
        # Asserted on the rendered document, not just the string:
        # sender labels bold, bodies plain.
        self.assertEqual(self._fragments("A"), [
            ("You: ", True),
            ("hello", False),
            ("fake-a:latest: ", True),
            ("Reply from fake-a:latest. ", False),
        ])

    def test_streaming_still_appends_tokens_mid_turn(self):
        self.client.delay = 0.5
        self.client.replies = {"fake-a:latest": ["one two three four five"]}
        self.state["composer"].setPlainText("slow one")
        self.state["send_a"].click()
        # Part of the reply is visible while the turn is still running.
        ok = self._wait_for(lambda: (
            "one two" in self._transcript("A")
            and self.state["state"]["busy"]), timeout=10)
        self.assertTrue(ok, "tokens should stream in mid-turn")
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self.assertIn("one two three four five", self._transcript("A"))

    def test_rerender_matches_live_format_exactly(self):
        self.assertTrue(self._send_to_a_and_wait("first message"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        live_text = self._transcript("A")
        live_frags = self._fragments("A")
        self.state["render_transcript"]("A")
        # The fake client streams "word + ' '" chunks, so the live text
        # carries a trailing space the stored history doesn't have;
        # real Ollama chunks concatenate exactly to the returned text.
        # Compare with trailing spaces stripped per line.
        def _normalized(text):
            return "\n".join(line.rstrip() for line in text.split("\n"))
        self.assertEqual(_normalized(self._transcript("A")),
                         _normalized(live_text))
        # Same normalization for the fragment texts: the trailing
        # space is the fake client's chunking, not the format.
        def _norm_frags(frags):
            return [(text.rstrip(), bold) for text, bold in frags]
        self.assertEqual(_norm_frags(self._fragments("A")),
                         _norm_frags(live_frags))

    def test_save_full_interleaves_in_time_order(self):
        self.client.replies = {
            "fake-a:latest": ["alpha one", "alpha two", "alpha three"],
            "fake-b:latest": ["beta one", "beta two", "beta three"]}
        self.assertTrue(self._send_both_and_wait("which is better?"))
        self._start_relay("Talk to each other.", cap=2)
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=10))
        self._run_task(0, 0, "Count the stars.")
        self.assertTrue(self._wait_idle())
        path = os.path.join(self.tmp.name, "full.txt")
        with self._save_dialog(path):
            self.state["save_full"].click()
        text = self._read(path)
        lines = text.split("\n")
        self.assertRegex(
            lines[0],
            r"^DUO transcript -- saved \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual(
            "\n".join(lines[1:]),
            "\nA -- You: which is better?\n\n"
            "B -- You: which is better?\n\n"
            "A -- fake-a:latest: alpha one\n\n"
            "B -- fake-b:latest: beta one\n\n"
            "A -- You: Talk to each other.\n\n"
            "A -- fake-a:latest: alpha two\n\n"
            "B -- You [relay from Model A]: alpha two\n\n"
            "B -- fake-b:latest: beta two\n\n"
            "A -- You [task]: Count the stars.\n\n"
            "A -- fake-a:latest: alpha three\n\n"
            "B -- You [task handoff]: "
            "Task result from Model A, for review.\n\n"
            "B -- fake-b:latest: beta three\n")
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Saved to %s" % path)

    def test_clear_removes_side_from_full_log(self):
        self.assertTrue(self._send_both_and_wait("hello both"))
        log = self.state["state"]["full_log"]
        self.assertEqual(len(log), 4)
        self.state["widgets"]["A"]["clear"].click()
        self.app.processEvents()
        self.assertEqual(
            self.state["state"]["full_log"],
            [("B", "You", "hello both"),
             ("B", "fake-b:latest", "Reply from fake-b:latest.")])
        # The export agrees: no A lines left.
        path = os.path.join(self.tmp.name, "full.txt")
        with self._save_dialog(path):
            self.state["save_full"].click()
        text = self._read(path)
        self.assertNotIn("A -- ", text)
        self.assertIn("B -- You: hello both", text)

    def test_reorder_and_delete_leave_full_log_untouched(self):
        self.client.replies = {"fake-a:latest": ["alpha one", "alpha two"]}
        self.assertTrue(self._send_to_a_and_wait("first message"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        before = list(self.state["state"]["full_log"])
        self.assertEqual(len(before), 4)
        self._open_history("A")
        lst = self.state["widgets"]["A"]["hist_list"]
        lst.setCurrentRow(2)  # "You: second message"
        self.app.processEvents()
        self.state["widgets"]["A"]["hist_up"].click()
        self.app.processEvents()
        lst.setCurrentRow(0)
        self.app.processEvents()
        self.state["widgets"]["A"]["hist_delete"].click()
        self.app.processEvents()
        # The history changed, but the log is what actually happened.
        self.assertEqual(
            [m["content"] for m in self.state["convo"].sides["A"].history],
            ["second message", "alpha one", "alpha two"])
        self.assertEqual(self.state["state"]["full_log"], before)

    def test_summarize_leaves_full_log_untouched(self):
        self.client.replies = {"fake-a:latest": [
            "alpha one", "alpha two", "alpha three",
            "They discussed fruit and picked apple pie."]}
        self.assertTrue(self._send_to_a_and_wait("first message"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        self.assertTrue(self._send_to_a_and_wait("third message"))
        before = list(self.state["state"]["full_log"])
        self.assertEqual(len(before), 6)
        self.state["widgets"]["A"]["summarize"].click()
        convo = self.state["convo"]
        ok = self._wait_for(lambda: (
            len(convo.sides["A"].history) == 5
            and convo.sides["A"].labels[0] == SUMMARY_LABEL
            and not self.state["state"]["busy"]), timeout=15)
        self.assertTrue(ok, "history should fold into summary + last 4")
        # The folded history changed; the log keeps the originals.
        self.assertEqual(self.state["state"]["full_log"], before)

    def test_save_full_empty_log(self):
        path = os.path.join(self.tmp.name, "empty.txt")
        with self._save_dialog(path):
            self.state["save_full"].click()
        text = self._read(path)
        lines = text.split("\n")
        self.assertRegex(
            lines[0],
            r"^DUO transcript -- saved \d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertEqual("\n".join(lines[1:]), "\nNo messages yet.\n")

    def test_save_full_cancel_writes_nothing(self):
        self.assertTrue(self._send_to_a_and_wait("hello"))
        before = set(os.listdir(self.tmp.name))
        with self._save_dialog(""):
            self.state["save_full"].click()
        self.assertEqual(set(os.listdir(self.tmp.name)), before)

    def test_save_full_disabled_mid_generation(self):
        save_full = self.state["save_full"]
        self.assertTrue(save_full.isEnabled())
        self.client.delay = 0.5
        self.client.replies = {"fake-a:latest": ["one two three four five"]}
        self.state["composer"].setPlainText("slow one")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["state"]["busy"], timeout=5))
        self.assertFalse(save_full.isEnabled())
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self.assertTrue(save_full.isEnabled())

    def test_save_full_disabled_mid_relay(self):
        save_full = self.state["save_full"]
        self.assertTrue(save_full.isEnabled())
        self.client.replies = {"fake-a:latest": [" ".join(["word"] * 100)],
                               "fake-b:latest": ["short"]}
        self._start_relay("Go on.", cap=10)
        self.assertTrue(self._wait_for(
            lambda: self.state["state"].get("relay") is not None,
            timeout=5))
        self.assertFalse(save_full.isEnabled())
        self.state["relay_btn"].click()
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=10))
        self.assertTrue(save_full.isEnabled())


# ---------------------------------------------------------------------------
# v0.10: chat retry, Start Ollama, debug log layer
# ---------------------------------------------------------------------------

class FlakyOllamaHandler(FakeOllamaHandler):
    """Fake Ollama that fails /api/chat in scripted ways.

    mode "fail_n": drop the first fail_n /api/chat requests (close the
    connection without answering), then serve normally.
    mode "fail_always": drop every /api/chat request. /api/tags still
    answers, so the probe says Ollama is up.
    mode "drop_after_two": stream two chunks, then cut the connection.
    chat_posts counts the /api/chat requests the server saw.
    """

    mode = "fail_n"
    fail_n = 0
    chat_posts = 0

    def _drop(self):
        # RST, not a clean FIN: a clean close after content would look
        # like a finished stream, and the client must see a failure.
        try:
            self.connection.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER,
                struct.pack("ii", 1, 0))
        except OSError:
            pass
        self.close_connection = True
        try:
            self.connection.close()
        except OSError:
            pass

    def do_POST(self):
        if self.path != "/api/chat":
            return super().do_POST()
        type(self).chat_posts += 1
        if self.mode == "fail_always":
            self._drop()
            return
        if self.mode == "fail_n" and type(self).chat_posts <= self.fail_n:
            self._drop()
            return
        if self.mode == "drop_after_two":
            # Stream two chunks without reading the request body, give
            # the client a moment to read them, then cut the stream.
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.end_headers()
            for chunk in ("Hello ", "from A."):
                line = json.dumps(
                    {"message": {"role": "assistant", "content": chunk},
                     "done": False}).encode("utf-8") + b"\n"
                self.wfile.write(line)
            self.wfile.flush()
            time.sleep(0.2)
            self._drop()
            return
        return super().do_POST()


def start_flaky_server(mode, fail_n=0):
    """A fake Ollama server with the given failure mode. Returns
    (server, handler_class, url); the caller shuts the server down."""
    class Flaky(FlakyOllamaHandler):
        pass

    Flaky.mode = mode
    Flaky.fail_n = fail_n
    Flaky.chat_posts = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), Flaky)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, Flaky, "http://127.0.0.1:%d" % server.server_address[1]


class OllamaClientRetryTests(unittest.TestCase):
    """Chat turns retry transient failures -- and only those."""

    def setUp(self):
        # Fast backoff in tests; the production pauses are seconds.
        self._patcher = mock.patch("dual_chat.CHAT_RETRY_DELAYS",
                                   (0.01, 0.01))
        self._patcher.start()
        self._servers = []

    def tearDown(self):
        self._patcher.stop()
        for server in self._servers:
            server.shutdown()
            server.server_close()

    def _start(self, mode, fail_n=0):
        server, handler, url = start_flaky_server(mode, fail_n)
        self._servers.append(server)
        return handler, url

    def test_retry_succeeds_after_two_failures(self):
        handler, url = self._start("fail_n", fail_n=2)
        client = OllamaClient(url)
        retries = []
        seen = []
        full = client.chat_stream(
            "fake-a:latest", [{"role": "user", "content": "hi"}],
            seen.append, threading.Event(),
            on_retry=lambda a, d, k: retries.append((a, d, k)))
        self.assertEqual(full, "Hello from A.")
        self.assertEqual(seen, ["Hello ", "from A."])
        self.assertEqual(handler.chat_posts, 3)
        self.assertEqual([attempt for attempt, _, _ in retries], [1, 2])

    def test_retry_exhausted_when_ollama_down(self):
        client = OllamaClient("http://127.0.0.1:9")  # nothing listens
        with self.assertRaises(OllamaError) as ctx:
            client.chat_stream(
                "fake-a:latest", [{"role": "user", "content": "hi"}],
                lambda t: None, threading.Event())
        self.assertEqual(str(ctx.exception), MSG_OLLAMA_DOWN)

    def test_retry_exhausted_when_ollama_slow(self):
        # The server answers /api/tags (so the probe says up) but drops
        # every /api/chat: slow/busy, never "not running".
        handler, url = self._start("fail_always")
        client = OllamaClient(url)
        with self.assertRaises(OllamaError) as ctx:
            client.chat_stream(
                "fake-a:latest", [{"role": "user", "content": "hi"}],
                lambda t: None, threading.Event())
        self.assertEqual(str(ctx.exception), MSG_OLLAMA_SLOW)
        self.assertEqual(handler.chat_posts, CHAT_MAX_ATTEMPTS)

    def test_no_retry_once_content_streamed(self):
        handler, url = self._start("drop_after_two")
        client = OllamaClient(url)
        seen = []
        with self.assertRaises(OllamaError) as ctx:
            client.chat_stream(
                "fake-a:latest", [{"role": "user", "content": "hi"}],
                seen.append, threading.Event())
        self.assertEqual(str(ctx.exception), MSG_OLLAMA_SLOW)
        self.assertEqual(seen, ["Hello ", "from A."])  # partial kept
        self.assertEqual(handler.chat_posts, 1)  # no retry, no duplicate

    def test_cancel_interrupts_backoff_sleep(self):
        # Long pauses: Stop must end the wait at once, not sleep
        # through it.
        with mock.patch("dual_chat.CHAT_RETRY_DELAYS", (30.0, 30.0)):
            client = OllamaClient("http://127.0.0.1:9")
            event = threading.Event()
            outcome = {}

            def run():
                try:
                    client.chat_stream(
                        "fake-a:latest",
                        [{"role": "user", "content": "hi"}],
                        lambda t: None, event)
                    outcome["result"] = "returned"
                except Cancelled:
                    outcome["result"] = "cancelled"
                except Exception as exc:  # noqa: BLE001 - record it
                    outcome["result"] = "error: %s" % exc

            thread = threading.Thread(target=run, daemon=True)
            started = time.monotonic()
            thread.start()
            time.sleep(0.3)
            event.set()
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(outcome.get("result"), "cancelled")
            self.assertLess(time.monotonic() - started, 5.0)

    def test_chat_timeout_matches_code_forge(self):
        self.assertEqual(CHAT_TIMEOUT, 1800)
        params = inspect.signature(OllamaClient.chat_stream).parameters
        self.assertEqual(params["timeout"].default, 1800)

    def test_down_and_slow_messages_are_distinct(self):
        self.assertNotEqual(MSG_OLLAMA_DOWN, MSG_OLLAMA_SLOW)
        self.assertIn("isn't running", MSG_OLLAMA_DOWN)
        self.assertIn("slow or busy", MSG_OLLAMA_SLOW)
        self.assertNotIn("isn't running", MSG_OLLAMA_SLOW)

    def test_probe_reports_up_and_down(self):
        _, url = self._start("fail_n")
        self.assertTrue(OllamaClient(url).probe())
        self.assertFalse(OllamaClient("http://127.0.0.1:9").probe())


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_FAKE_OLLAMA_SCRIPT = """#!/usr/bin/env python3
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = {port}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        body = b'{"models": []}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if sys.argv[1:] == ["serve"]:
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
"""

_FAKE_OLLAMA_SLEEPER = """#!/usr/bin/env python3
import time

time.sleep(120)
"""


class OllamaServerManagerTests(unittest.TestCase):
    """Start Ollama from DUO, with a fake `ollama` on PATH."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="duo-ollama-test-")
        self.managers = []

    def tearDown(self):
        for manager in self.managers:
            manager.stop()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _write_fake_ollama(self, script):
        path = os.path.join(self.tmpdir, "ollama")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(script)
        os.chmod(path, 0o755)
        return path

    def _path_with_tmpdir(self):
        return mock.patch.dict(
            os.environ,
            {"PATH": self.tmpdir + os.pathsep + os.environ["PATH"]})

    def test_start_spawns_and_reports_running(self):
        port = _free_port()
        self._write_fake_ollama(
            _FAKE_OLLAMA_SCRIPT.replace("{port}", str(port)))
        client = OllamaClient("http://127.0.0.1:%d" % port)
        manager = OllamaServerManager(client)
        self.managers.append(manager)
        with self._path_with_tmpdir():
            ok, message = manager.start()
        self.assertTrue(ok)
        self.assertEqual(message, "Ollama is running.")
        self.assertTrue(manager.running)
        self.assertTrue(client.probe())

    def test_stop_ends_only_duo_started_server(self):
        port = _free_port()
        self._write_fake_ollama(
            _FAKE_OLLAMA_SCRIPT.replace("{port}", str(port)))
        client = OllamaClient("http://127.0.0.1:%d" % port)
        manager = OllamaServerManager(client)
        self.managers.append(manager)
        with self._path_with_tmpdir():
            ok, _ = manager.start()
        self.assertTrue(ok)
        manager.stop()
        self.assertFalse(manager.running)
        self.assertFalse(client.probe())

    def test_start_when_already_running_spawns_nothing(self):
        server, url = start_fake_server()
        try:
            manager = OllamaServerManager(OllamaClient(url))
            self.managers.append(manager)
            ok, message = manager.start()
            self.assertTrue(ok)
            self.assertEqual(message, "Ollama is running.")
            self.assertFalse(manager.running)
        finally:
            server.shutdown()
            server.server_close()

    def test_start_without_ollama_installed(self):
        client = OllamaClient("http://127.0.0.1:9")
        manager = OllamaServerManager(client)
        self.managers.append(manager)
        with mock.patch("dual_chat.shutil.which", return_value=None):
            ok, message = manager.start()
        self.assertFalse(ok)
        self.assertEqual(message, MSG_OLLAMA_NOT_INSTALLED)

    def test_start_times_out_when_server_never_answers(self):
        self._write_fake_ollama(_FAKE_OLLAMA_SLEEPER)
        port = _free_port()
        client = OllamaClient("http://127.0.0.1:%d" % port)
        manager = OllamaServerManager(client)
        self.managers.append(manager)
        with self._path_with_tmpdir():
            ok, message = manager.start(timeout=1)
        self.assertFalse(ok)
        self.assertEqual(message, MSG_OLLAMA_START_FAILED)

    def test_stop_without_start_is_a_noop(self):
        manager = OllamaServerManager(OllamaClient("http://127.0.0.1:9"))
        manager.stop()  # must not raise


class DebugFilenameTests(unittest.TestCase):
    def test_default_debug_filename(self):
        day = datetime.date(2026, 11, 12)
        self.assertEqual(default_debug_filename(day),
                         "duo-debug-report-2026-11-12.txt")
        self.assertTrue(default_debug_filename().startswith(
            "duo-debug-report-"))


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class StartOllamaGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        self.client.reset()
        self.window = build_window(self.client, logger=TEST_LOGGER)
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

    def test_start_button_hidden_by_default(self):
        self.assertTrue(self.state["start_ollama_btn"].isHidden())

    def test_start_button_appears_when_models_fail(self):
        from dual_chat import build_window

        class DownClient:
            def list_models(self, timeout=5):
                raise OllamaError(MSG_OLLAMA_DOWN)

            def chat_stream(self, *a, **k):
                raise OllamaError(MSG_OLLAMA_DOWN)

            def probe(self, timeout=5):
                return False

            def running_models(self, timeout=5):
                return {}

        window = build_window(DownClient(), logger=TEST_LOGGER)
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state["start_ollama_btn"].isVisible(),
                timeout=10))
        finally:
            window.close()

    def test_slow_failure_shows_button_and_note(self):
        from dual_chat import build_window

        class SlowClient(FakeGuiClient):
            def chat_stream(self, model, messages, on_chunk,
                            cancel_event, timeout=1800, on_metrics=None,
                            on_retry=None, options=None):
                raise OllamaError(MSG_OLLAMA_SLOW)

        window = build_window(SlowClient(), logger=TEST_LOGGER)
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state["widgets"]["A"]["picker"].count() == 2))
            state["widgets"]["A"]["picker"].setCurrentIndex(0)
            self.app.processEvents()
            state["composer"].setPlainText("hello")
            state["send_a"].click()
            pane = state["widgets"]["A"]["transcript"]
            self.assertTrue(self._wait_for(
                lambda: "slow or busy" in pane.toPlainText(), timeout=10))
            self.assertTrue(state["start_ollama_btn"].isVisible())
        finally:
            window.close()

    def test_slow_relay_stop_has_no_double_period(self):
        from dual_chat import build_window

        class SlowClient(FakeGuiClient):
            def chat_stream(self, model, messages, on_chunk,
                            cancel_event, timeout=1800, on_metrics=None,
                            on_retry=None, options=None):
                raise OllamaError(MSG_OLLAMA_SLOW)

        window = build_window(SlowClient(), logger=TEST_LOGGER)
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state["widgets"]["A"]["picker"].count() == 2))
            state["widgets"]["A"]["picker"].setCurrentIndex(0)
            state["widgets"]["B"]["picker"].setCurrentIndex(1)
            self.app.processEvents()
            state["cap_spin"].setValue(2)
            state["composer"].setPlainText("go")
            state["relay_btn"].click()
            self.assertTrue(self._wait_for(
                lambda: state["relay_status"].text().startswith(
                    "Relay stopped"), timeout=10))
            self.assertNotIn("..", state["relay_status"].text())
            self.assertTrue(
                state["relay_status"].text().endswith("moment."))
        finally:
            window.close()

    def test_model_failure_keeps_button_hidden(self):
        from dual_chat import build_window

        class BadModelClient(FakeGuiClient):
            def chat_stream(self, model, messages, on_chunk,
                            cancel_event, timeout=1800, on_metrics=None,
                            on_retry=None, options=None):
                raise OllamaError(MSG_MODEL_FAILED)

        window = build_window(BadModelClient(), logger=TEST_LOGGER)
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state["widgets"]["A"]["picker"].count() == 2))
            state["widgets"]["A"]["picker"].setCurrentIndex(0)
            self.app.processEvents()
            state["composer"].setPlainText("hello")
            state["send_a"].click()
            pane = state["widgets"]["A"]["transcript"]
            self.assertTrue(self._wait_for(
                lambda: "couldn't be loaded" in pane.toPlainText(),
                timeout=10))
            self.assertTrue(state["start_ollama_btn"].isHidden())
        finally:
            window.close()

    def test_click_start_when_already_running(self):
        # The fake client probes True, so the real manager reports
        # running without spawning anything.
        self.state["show_start_ollama"](True)
        self.app.processEvents()
        self.assertTrue(self.state["start_ollama_btn"].isVisible())
        self.state["start_ollama_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["start_ollama_btn"].isHidden(),
            timeout=10))
        self.assertFalse(self.state["ollama_manager"].running)

    def test_click_start_failure_keeps_button(self):
        manager = self.state["ollama_manager"]
        manager.start = lambda timeout=None: (
            False, "Ollama didn't start. Try starting it yourself.")
        self.state["show_start_ollama"](True)
        self.app.processEvents()
        self.state["start_ollama_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: "didn't start" in
            self.window.statusBar().currentMessage(), timeout=10))
        self.assertTrue(self.state["start_ollama_btn"].isVisible())
        self.assertTrue(self.state["start_ollama_btn"].isEnabled())

    def test_close_stops_duo_started_server(self):
        manager = self.state["ollama_manager"]
        recorder = mock.Mock()
        manager.stop = recorder
        self.window.close()
        self.app.processEvents()
        self.assertTrue(recorder.called)


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class DebugLogGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        import debug_log
        self.client.reset()
        self.tmp = tempfile.TemporaryDirectory()
        # A unique logger name per test: DebugLogger stacks handlers
        # onto its named logger, so sharing a name would mix logs.
        self.logger = debug_log.DebugLogger(
            "duo-debug-" + self.id().replace(".", "-"),
            APP_VERSION, log_dir=self.tmp.name)
        self.window = build_window(self.client, logger=self.logger)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self._pick_models()

    def tearDown(self):
        self.window.close()
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

    def _save_dialog(self, path):
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getSaveFileName",
            return_value=(path, "Text files (*.txt)"))

    def _read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _log_text(self):
        return self._read(self.logger.log_path)

    def _send_to_a_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_a"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def test_debug_report_button_writes_report(self):
        path = os.path.join(self.tmp.name, "report.txt")
        with self._save_dialog(path):
            self.state["debug_btn"].click()
        text = self._read(path)
        self.assertIn("DEBUG REPORT", text)
        self.assertIn(APP_VERSION, text)
        self.assertIn("ollama", text.lower())
        self.assertIn("Recent actions", text)
        self.assertIn("DUO %s started" % APP_VERSION, text)
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Debug report saved to %s" % path)

    def test_debug_report_cancel_writes_nothing(self):
        before = sorted(os.listdir(self.tmp.name))
        with self._save_dialog(""):
            self.state["debug_btn"].click()
        self.assertEqual(sorted(os.listdir(self.tmp.name)), before)

    def test_log_never_holds_message_contents(self):
        marker = "PURPLE ELEPHANT 4821"
        self.assertTrue(self._send_to_a_and_wait(marker))
        log = self._log_text()
        self.assertNotIn(marker, log)
        self.assertIn("send to A", log)  # the send WAS logged, sans text
        report_path = os.path.join(self.tmp.name, "report.txt")
        self.logger.generate_report(report_path)
        self.assertNotIn(marker, self._read(report_path))

    def test_log_records_relay_and_turns(self):
        self.state["start_picker"].setCurrentIndex(0)
        self.state["cap_spin"].setValue(2)
        self.state["mode_picker"].setCurrentIndex(0)  # Blind
        self.state["composer"].setPlainText("ZEBRA GIRAFFE 7394")
        self.state["relay_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["relay_status"].text().startswith(
                "Relay stopped"), timeout=15))
        log = self._log_text()
        self.assertIn("relay started", log)
        self.assertIn("relay turn", log)
        self.assertIn("relay ended", log)
        self.assertIn("turn done", log)
        self.assertNotIn("ZEBRA GIRAFFE", log)


# ---------------------------------------------------------------------------
# Phone deck (v0.11)
# ---------------------------------------------------------------------------

class LanIpTests(unittest.TestCase):
    def test_returns_a_valid_ipv4_address(self):
        ip = get_lan_ip()
        parts = ip.split(".")
        self.assertEqual(len(parts), 4)
        for part in parts:
            self.assertTrue(part.isdigit())
            self.assertLessEqual(int(part), 255)
        self.assertNotEqual(ip, "0.0.0.0")

    def test_prefers_a_real_address_over_loopback(self):
        # In this sandbox there is a route, so the answer is a real
        # LAN address, not loopback. (On a machine with no network
        # at all the fallback is 127.0.0.1 -- that path is the
        # except-OSError branch, not this test.)
        self.assertNotEqual(get_lan_ip(), "127.0.0.1")


class DeckStateTests(unittest.TestCase):
    def _state(self, **over):
        state = {"active": set(), "queued": set(), "relay": None,
                 "busy": False, "summarizing": None}
        state.update(over)
        return state

    def test_empty_conversation_shape(self):
        convo = Conversation()
        snap = build_deck_state(convo, self._state())
        self.assertEqual(set(snap), {"sides", "active_side", "busy",
                                     "relay", "version"})
        self.assertEqual(snap["sides"]["A"],
                         {"model": None, "messages": []})
        self.assertEqual(snap["sides"]["B"],
                         {"model": None, "messages": []})
        self.assertIsNone(snap["active_side"])
        self.assertFalse(snap["busy"])
        self.assertIsNone(snap["relay"])
        self.assertEqual(snap["version"], APP_VERSION)

    def test_messages_carry_labels_and_display_text(self):
        convo = Conversation()
        convo.sides["A"].model = "fake-a:latest"
        convo.sides["A"].add_user("File: notes.txt\nSECRET-CONTENT",
                                  display="hello\n(Attached: notes.txt)")
        convo.sides["A"].add_assistant("answer one")
        snap = build_deck_state(convo, self._state())
        msgs = snap["sides"]["A"]["messages"]
        self.assertEqual(
            msgs[0],
            {"label": "You", "text": "hello\n(Attached: notes.txt)"})
        self.assertEqual(
            msgs[1], {"label": "fake-a:latest", "text": "answer one"})
        # The deck shows what the window shows -- never file contents.
        self.assertNotIn("SECRET-CONTENT", json.dumps(snap))

    def test_active_side_prefers_generating_then_queued(self):
        convo = Conversation()
        self.assertEqual(
            build_deck_state(
                convo, self._state(active={"B"}))["active_side"], "B")
        self.assertEqual(
            build_deck_state(
                convo, self._state(queued={"A"}))["active_side"], "A")
        self.assertEqual(
            build_deck_state(
                convo,
                self._state(active={"B"}, queued={"A"}))["active_side"],
            "B")
        self.assertEqual(
            build_deck_state(
                convo,
                self._state(summarizing="A"))["active_side"], "A")

    def test_relay_status_comes_through(self):
        convo = Conversation()
        session = RelaySession("A", turn_cap=10)
        session.turns_done = 2
        session.current_side = "B"
        session.hop_from = "A"
        snap = build_deck_state(convo, self._state(relay=session))
        self.assertEqual(snap["relay"], "Relay on: turn 3/10, A to B")


class DeckPageTests(unittest.TestCase):
    def test_page_has_chat_target_and_export(self):
        self.assertIn('data-t="A"', DECK_PAGE_HTML)
        self.assertIn('data-t="B"', DECK_PAGE_HTML)
        self.assertIn('data-t="Both"', DECK_PAGE_HTML)
        self.assertIn('id="send"', DECK_PAGE_HTML)
        self.assertIn('href="/transcript"', DECK_PAGE_HTML)
        self.assertIn("Export transcript", DECK_PAGE_HTML)
        self.assertIn('fetch("/state")', DECK_PAGE_HTML)
        self.assertIn('fetch("/send"', DECK_PAGE_HTML)
        self.assertIn("thinking...", DECK_PAGE_HTML)

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_served_script_passes_node_check(self):
        # The page is a plain Python string: escapes in its JS can
        # corrupt the served page while the source looks fine. Check
        # the script that actually ships, not a grep.
        import re
        import subprocess
        match = re.search(r"<script>(.*?)</script>", DECK_PAGE_HTML, re.S)
        self.assertIsNotNone(match)
        with tempfile.NamedTemporaryFile("w", suffix=".js",
                                         delete=False) as fh:
            fh.write(match.group(1))
            path = fh.name
        try:
            out = subprocess.run(["node", "--check", path],
                                 capture_output=True, text=True, timeout=30)
            self.assertEqual(out.returncode, 0, out.stderr)
        finally:
            os.unlink(path)


class QrCodeTests(unittest.TestCase):
    def test_encoder_produces_a_valid_matrix(self):
        import qrcodegen
        qr = qrcodegen.QrCode.encode_text(
            "http://192.168.1.42:8765/", qrcodegen.QrCode.Ecc.MEDIUM)
        size = qr.get_size()
        self.assertGreaterEqual(size, 21)
        self.assertEqual(size % 2, 1)  # QR sizes are odd
        # Finder patterns: solid 7x7 corners, top-left/top-right/
        # bottom-left, with a dark centre module and a light ring.
        for (ox, oy) in ((0, 0), (size - 7, 0), (0, size - 7)):
            self.assertTrue(qr.get_module(ox, oy))
            self.assertTrue(qr.get_module(ox + 6, oy))
            self.assertTrue(qr.get_module(ox, oy + 6))
            self.assertTrue(qr.get_module(ox + 6, oy + 6))
            self.assertTrue(qr.get_module(ox + 3, oy + 3))
            self.assertFalse(qr.get_module(ox + 1, oy + 1))


class DeckServerTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.server = DeckServer(
            get_state=lambda: {"sides": {}, "active_side": None,
                               "busy": False, "relay": None,
                               "version": APP_VERSION},
            send_message=lambda text, target: (
                self.sent.append((text, target)) or None),
            get_transcript=lambda: "FULL TRANSCRIPT HERE",
            host="127.0.0.1", port=0)
        self.assertTrue(self.server.start())
        self.base = "http://127.0.0.1:%d" % self.server.port

    def tearDown(self):
        self.server.stop()

    def _get(self, path):
        import urllib.request
        return urllib.request.urlopen(self.base + path, timeout=5)

    def _post(self, path, payload):
        import urllib.request
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data,
            headers={"Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=5)

    def test_root_serves_the_deck_page(self):
        resp = self._get("/")
        self.assertEqual(resp.status, 200)
        body = resp.read().decode("utf-8")
        self.assertIn("<title>DUO</title>", body)
        self.assertIn("Export transcript", body)

    def test_state_json_shape(self):
        state = json.loads(self._get("/state").read())
        self.assertEqual(set(state), {"sides", "active_side", "busy",
                                     "relay", "version"})

    def test_send_validation(self):
        import urllib.request
        # Missing message.
        res = json.loads(self._post("/send", {"target": "A"}).read())
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], MSG_EMPTY)
        # Blank message.
        res = json.loads(self._post(
            "/send", {"text": "  ", "target": "A"}).read())
        self.assertFalse(res["ok"])
        # Missing target.
        res = json.loads(self._post("/send", {"text": "hi"}).read())
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], MSG_PHONE_TARGET)
        # Bad target.
        res = json.loads(self._post(
            "/send", {"text": "hi", "target": "C"}).read())
        self.assertFalse(res["ok"])
        # Malformed JSON.
        req = urllib.request.Request(
            self.base + "/send", data=b"{not json",
            headers={"Content-Type": "application/json"})
        res = json.loads(urllib.request.urlopen(req, timeout=5).read())
        self.assertFalse(res["ok"])
        # Nothing reached the callback.
        self.assertEqual(self.sent, [])

    def test_send_reaches_callback(self):
        res = json.loads(self._post(
            "/send", {"text": "hello", "target": "Both"}).read())
        self.assertTrue(res["ok"])
        self.assertEqual(self.sent, [("hello", "Both")])

    def test_send_error_passes_through(self):
        self.server.send_message = lambda text, target: "NOPE"
        res = json.loads(self._post(
            "/send", {"text": "hi", "target": "A"}).read())
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "NOPE")

    def test_transcript_download(self):
        resp = self._get("/transcript")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.read().decode("utf-8"),
                         "FULL TRANSCRIPT HERE")
        self.assertIn("attachment;", resp.headers["Content-Disposition"])
        self.assertIn("duo-full-", resp.headers["Content-Disposition"])

    def test_unknown_paths_404(self):
        import urllib.error
        for path in ("/nope", "/state/x"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self._get(path)
            self.assertEqual(cm.exception.code, 404)

    def test_port_fallback_when_busy(self):
        # Occupy a port, then start a server on it: it must move to
        # the next free port instead of failing.
        blocker = socket.socket()
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        port = blocker.getsockname()[1]
        try:
            other = DeckServer(lambda: {}, lambda t, g: None, lambda: "",
                               host="127.0.0.1", port=port)
            self.assertTrue(other.start())
            self.assertNotEqual(other.port, port)
            other.stop()
        finally:
            blocker.close()

    def test_stop_closes_the_port(self):
        import urllib.error
        port = self.server.port
        self.server.stop()
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(
                "http://127.0.0.1:%d/" % port, timeout=5)


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class DeckGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        self.client.reset()
        self.window = build_window(self.client, logger=TEST_LOGGER,
                                   deck_port=0)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(
            lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self.base = "http://127.0.0.1:%d" % self.state["deck_server"].port

    def tearDown(self):
        dialog = getattr(self.window, "_phone_deck_dialog", None)
        if dialog is not None:
            dialog.close()
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

    def _request(self, method, path, payload=None):
        """One HTTP request in a worker thread while the test thread
        pumps the event loop -- a phone send waits on the GUI thread,
        so a blocking call from the test thread would deadlock."""
        import urllib.request
        result = {}

        def run():
            try:
                if payload is None:
                    resp = urllib.request.urlopen(
                        self.base + path, timeout=20)
                else:
                    data = json.dumps(payload).encode("utf-8")
                    req = urllib.request.Request(
                        self.base + path, data=data,
                        headers={"Content-Type": "application/json"})
                    resp = urllib.request.urlopen(req, timeout=20)
                result["body"] = resp.read()
                result["headers"] = resp.headers
                result["status"] = resp.status
            except Exception as exc:  # noqa: BLE001 - surfaced below
                result["error"] = exc

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        ok = self._wait_for(lambda: not thread.is_alive(), timeout=25)
        self.assertTrue(ok, "deck request timed out: %s %s"
                        % (method, path))
        if "error" in result:
            raise result["error"]
        return result

    def _get_json(self, path):
        return json.loads(self._request("GET", path)["body"])

    def _post_send(self, payload):
        return json.loads(self._request("POST", "/send", payload)["body"])

    def test_server_starts_with_the_app(self):
        server = self.state["deck_server"]
        self.assertIsNotNone(server)
        self.assertTrue(server.running)
        self.assertGreater(server.port, 0)
        self.assertTrue(server.url.startswith("http://"))
        self.assertTrue(server.url.endswith(":%d/" % server.port))

    def test_state_shape_from_the_real_window(self):
        state = self._get_json("/state")
        self.assertEqual(set(state), {"sides", "active_side", "busy",
                                     "relay", "version"})
        self.assertIsNone(state["sides"]["A"]["model"])
        self.assertEqual(state["sides"]["A"]["messages"], [])
        self.assertIsNone(state["active_side"])
        self.assertIsNone(state["relay"])

    def test_state_follows_a_send(self):
        self._pick_models()
        self.assertTrue(
            self._post_send({"text": "Deck hello", "target": "A"})["ok"])
        self.assertTrue(self._wait_for(lambda: (
            "Reply from fake-a:latest." in
            self.state["widgets"]["A"]["transcript"].toPlainText()),
            timeout=15))
        state = self._get_json("/state")
        msgs = state["sides"]["A"]["messages"]
        self.assertEqual(msgs[0], {"label": "You", "text": "Deck hello"})
        self.assertEqual(msgs[1]["label"], "fake-a:latest")
        self.assertIn("Reply from fake-a:latest.", msgs[1]["text"])
        self.assertEqual(state["sides"]["A"]["model"], "fake-a:latest")

    def test_phone_send_reaches_the_right_side(self):
        self._pick_models()
        self.client.calls.clear()
        res = self._post_send({"text": "Just for A.", "target": "A"})
        self.assertTrue(res["ok"])
        self.assertTrue(self._wait_for(lambda: (
            "Reply from fake-a:latest." in
            self.state["widgets"]["A"]["transcript"].toPlainText()
            and len(self.state["convo"].sides["A"].history) == 2),
            timeout=15))
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        self.assertEqual(self.state["convo"].sides["B"].history, [])
        self.assertEqual(
            [m["content"] for m in self.state["convo"].sides["A"].history],
            ["Just for A.", "Reply from fake-a:latest."])

    def test_phone_send_to_both(self):
        self._pick_models()
        res = self._post_send({"text": "Both, please.", "target": "Both"})
        self.assertTrue(res["ok"])
        self.assertTrue(self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) == 2
            and len(self.state["convo"].sides["B"].history) == 2),
            timeout=15))
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest"])

    def test_phone_send_validation(self):
        # Nothing picked yet: the model error comes back plainly.
        res = self._post_send({"text": "hi", "target": "A"})
        self.assertFalse(res["ok"])
        self.assertIn("Pick a model", res["error"])
        self._pick_models()
        # Empty message.
        res = self._post_send({"text": "   ", "target": "A"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], MSG_EMPTY)
        # No target.
        res = self._post_send({"text": "hi"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], MSG_PHONE_TARGET)
        # Nothing was sent.
        self.assertEqual(self.client.calls, [])

    def test_phone_send_rejected_when_busy(self):
        self._pick_models()
        self.state["state"]["busy"] = True
        try:
            res = self._post_send({"text": "hi", "target": "A"})
        finally:
            self.state["state"]["busy"] = False
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], MSG_PHONE_BUSY)
        self.assertEqual(self.client.calls, [])

    def test_phone_send_rejected_during_relay(self):
        self._pick_models()
        session = RelaySession("A", turn_cap=10)
        self.state["state"]["relay"] = session
        try:
            res = self._post_send({"text": "hi", "target": "A"})
        finally:
            self.state["state"]["relay"] = None
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], MSG_PHONE_RELAY)
        self.assertEqual(self.client.calls, [])

    def test_phone_send_carries_no_desktop_attachments(self):
        self._pick_models()
        with tempfile.NamedTemporaryFile("w", suffix=".txt",
                                         delete=False) as fh:
            fh.write("DESKTOP-SECRET-CONTENT")
            path = fh.name
        try:
            self.state["add_attachments"]([path])
            self.assertEqual(len(self.state["state"]["attachments"]), 1)
            res = self._post_send({"text": "Phone hello", "target": "A"})
            self.assertTrue(res["ok"])
            self.assertTrue(self._wait_for(lambda: (
                len(self.state["convo"].sides["A"].history) == 2),
                timeout=15))
            sent = self.client.messages_seen[-1]
            self.assertNotIn("DESKTOP-SECRET-CONTENT", json.dumps(sent))
            # And the desktop attachment is still pending, untouched.
            self.assertEqual(len(self.state["state"]["attachments"]), 1)
        finally:
            os.unlink(path)

    def test_transcript_download_matches_desktop_export(self):
        self._pick_models()
        self.assertTrue(
            self._post_send({"text": "One", "target": "Both"})["ok"])
        self.assertTrue(self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) == 2
            and len(self.state["convo"].sides["B"].history) == 2),
            timeout=15))
        body = self._request("GET", "/transcript")["body"].decode("utf-8")
        expected = format_full_transcript(
            list(self.state["state"]["full_log"]))

        def _norm(text):
            # The header carries a minute stamp; normalize it so a
            # test straddling a minute boundary can't flake.
            lines = text.split("\n")
            lines[0] = "HEADER"
            return "\n".join(lines)

        self.assertEqual(_norm(body), _norm(expected))
        self.assertRegex(body.split("\n")[0],
                         r"^DUO transcript -- saved "
                         r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_qr_dialog_shows_code_and_url(self):
        from PySide6.QtWidgets import QLabel
        self.state["phone_btn"].click()
        self.app.processEvents()
        dialog = getattr(self.window, "_phone_deck_dialog", None)
        self.assertIsNotNone(dialog)
        self.assertTrue(dialog.isVisible())
        labels = dialog.findChildren(QLabel)
        pixmaps = [lbl.pixmap() for lbl in labels
                   if lbl.pixmap() is not None
                   and not lbl.pixmap().isNull()]
        self.assertEqual(len(pixmaps), 1)
        self.assertFalse(pixmaps[0].isNull())
        self.assertGreater(pixmaps[0].width(), 100)
        texts = [lbl.text() for lbl in labels]
        self.assertIn(self.state["deck_server"].url, texts)
        dialog.close()

    def test_qr_pixmap_renders(self):
        pixmap = render_qr_pixmap("http://192.168.1.42:8765/")
        self.assertIsNotNone(pixmap)
        self.assertFalse(pixmap.isNull())
        self.assertEqual(pixmap.width(), pixmap.height())
        self.assertGreater(pixmap.width(), 100)

    def test_server_stops_when_the_window_closes(self):
        import urllib.error
        port = self.state["deck_server"].port
        self.window.close()
        self.app.processEvents()
        with self.assertRaises(urllib.error.URLError):
            urllib.request.urlopen(
                "http://127.0.0.1:%d/" % port, timeout=5)


# ---------------------------------------------------------------------------
# v0.13: keyboard send + composer cap removed (audit T2)
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class KeyboardSendGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        # The "Enter to send" tick is persisted, so clear it before
        # every window: each test starts from the default (off).
        self._clear_enter_to_send()
        from dual_chat import build_window
        self.window = build_window(self.client, logger=TEST_LOGGER)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)

    def tearDown(self):
        self.window.close()
        self._clear_enter_to_send()

    def _clear_enter_to_send(self):
        from PySide6.QtCore import QSettings
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        settings.remove(SETTING_ENTER_TO_SEND)
        settings.sync()

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

    def _key_click(self, key, modifiers=None):
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest
        composer = self.state["composer"]
        composer.setFocus()
        self.app.processEvents()
        if modifiers is None:
            modifiers = Qt.KeyboardModifier.NoModifier
        QTest.keyClick(composer, key, modifiers)
        self.app.processEvents()

    def test_ctrl_enter_sends_to_both(self):
        from PySide6.QtCore import Qt
        self._pick_models()
        self.client.calls.clear()
        self.state["composer"].setPlainText("Hello, both of you.")
        self._key_click(Qt.Key_Return, Qt.ControlModifier)
        ok = self._wait_for(lambda: (
            "Reply from fake-a:latest." in
            self.state["widgets"]["A"]["transcript"].toPlainText()
            and "Reply from fake-b:latest." in
            self.state["widgets"]["B"]["transcript"].toPlainText()
            and len(self.state["convo"].sides["A"].history) == 2
            and len(self.state["convo"].sides["B"].history) == 2),
            timeout=15)
        self.assertTrue(ok, "Ctrl+Enter should send to both panes")
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest"])
        # The composer clears after a send, same as the button.
        self.assertEqual(self.state["composer"].toPlainText(), "")

    def test_ctrl_enter_empty_shows_note_and_sends_nothing(self):
        from PySide6.QtCore import Qt
        self._pick_models()
        self.client.calls.clear()
        self._key_click(Qt.Key_Return, Qt.ControlModifier)
        self.assertEqual(self.window.statusBar().currentMessage(),
                         MSG_EMPTY)
        self.assertEqual(self.client.calls, [])

    def test_ctrl_enter_without_models_does_nothing(self):
        # No models picked: Send to Both is disabled, so Ctrl+Enter
        # must do nothing at all -- no send, and no newline either.
        from PySide6.QtCore import Qt
        self.client.calls.clear()
        self.state["composer"].setPlainText("Hello?")
        self._key_click(Qt.Key_Return, Qt.ControlModifier)
        self.assertEqual(self.client.calls, [])
        self.assertEqual(self.state["composer"].toPlainText(), "Hello?")

    def test_enter_to_send_on_enter_sends_shift_enter_newline(self):
        from PySide6.QtCore import Qt
        self._pick_models()
        self.client.calls.clear()
        self.state["enter_send_check"].setChecked(True)
        self.app.processEvents()
        composer = self.state["composer"]
        composer.setPlainText("first line")
        # Shift+Enter adds a newline, sends nothing.
        self._key_click(Qt.Key_Return, Qt.ShiftModifier)
        self.assertEqual(self.client.calls, [])
        self.assertIn("\n", composer.toPlainText())
        self.assertIn("first line", composer.toPlainText())
        # Plain Enter sends it all.
        self._key_click(Qt.Key_Return)
        ok = self._wait_for(lambda: (
            "Reply from fake-a:latest." in
            self.state["widgets"]["A"]["transcript"].toPlainText()
            and "Reply from fake-b:latest." in
            self.state["widgets"]["B"]["transcript"].toPlainText()),
            timeout=15)
        self.assertTrue(ok, "Enter should send with the box ticked")
        self.assertEqual(composer.toPlainText(), "")

    def test_enter_to_send_off_enter_is_newline(self):
        from PySide6.QtCore import Qt
        self.assertFalse(self.state["enter_send_check"].isChecked())
        self._pick_models()
        self.client.calls.clear()
        composer = self.state["composer"]
        composer.setPlainText("first line")
        self._key_click(Qt.Key_Return)
        self.assertEqual(self.client.calls, [])
        self.assertIn("\n", composer.toPlainText())
        self.assertIn("first line", composer.toPlainText())

    def test_enter_to_send_persists_across_restart(self):
        from dual_chat import build_window
        self.state["enter_send_check"].setChecked(True)
        self.app.processEvents()
        self.window.close()
        self.app.processEvents()
        window2 = build_window(self.client, logger=TEST_LOGGER)
        try:
            window2.show()
            state2 = window2._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state2["widgets"]["A"]["picker"].count() == 2))
            self.assertTrue(state2["enter_send_check"].isChecked())
        finally:
            window2.close()

    def test_long_paste_keeps_every_paragraph(self):
        composer = self.state["composer"]
        paragraphs = ["paragraph %d" % i for i in range(12)]
        composer.insertPlainText("\n".join(paragraphs))
        self.app.processEvents()
        self.assertEqual(composer.toPlainText(), "\n".join(paragraphs))
        self.assertEqual(composer.blockCount(), 12)


# ---------------------------------------------------------------------------
# First-run guard (v0.14, audit T3)
# ---------------------------------------------------------------------------

class FirstRunGuardTests(unittest.TestCase):
    """A fresh install with PySide6 missing must fail plainly, never
    with a traceback: the app guard, the installer's post-install
    check, and run.sh with no .venv."""

    # The post-install check, exactly as install.sh runs it. Kept in
    # sync with install.sh by
    # test_install_sh_pins_pyside6_and_contains_the_check.
    INSTALL_CHECK = (
        "import importlib.metadata; "
        "importlib.metadata.version('PySide6-Essentials')")

    def _tree(self):
        return os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))

    def test_main_without_pyside6_prints_plain_message_and_exits(self):
        # Run main() in a subprocess with PySide6 blocked at import
        # time: exit non-zero, plain message on stderr, no traceback
        # anywhere. DISPLAY is stripped so no dialog can appear.
        blocker = (
            "import importlib.abc, importlib.machinery, sys\n"
            "class _RaisingLoader(importlib.abc.Loader):\n"
            "    def create_module(self, spec):\n"
            "        return None\n"
            "    def exec_module(self, module):\n"
            "        raise ImportError(\"No module named 'PySide6' "
            "(blocked for test)\")\n"
            "class _BlockPySide6(importlib.abc.MetaPathFinder):\n"
            "    def find_spec(self, fullname, path=None, target=None):\n"
            "        if fullname == 'PySide6' or "
            "fullname.startswith('PySide6.'):\n"
            "            return importlib.machinery.ModuleSpec("
            "fullname, _RaisingLoader())\n"
            "        return None\n"
            "sys.meta_path.insert(0, _BlockPySide6())\n"
            "sys.path.insert(0, %r)\n"
            "import dual_chat\n"
            "sys.exit(dual_chat.main([]))\n" % self._tree()
        )
        env = dict(os.environ)
        env.pop("DISPLAY", None)
        proc = subprocess.run(
            [sys.executable, "-c", blocker],
            capture_output=True, text=True, timeout=60, env=env)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("PySide6", proc.stderr)
        self.assertIn("install.sh", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertNotIn("Traceback", proc.stdout)

    def test_install_check_fails_without_pyside6_and_passes_with_it(self):
        # With PySide6 present the check passes...
        present = subprocess.run(
            [sys.executable, "-c", self.INSTALL_CHECK],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(present.returncode, 0)
        # ...and a bare venv with no PySide6 fails it.
        venv_dir = tempfile.mkdtemp(prefix="duo-no-pyside6-")
        try:
            subprocess.run(
                [sys.executable, "-m", "venv", venv_dir],
                capture_output=True, timeout=120, check=True)
            venv_python = os.path.join(venv_dir, "bin", "python")
            absent = subprocess.run(
                [venv_python, "-c", self.INSTALL_CHECK],
                capture_output=True, text=True, timeout=60)
            self.assertNotEqual(absent.returncode, 0)
        finally:
            shutil.rmtree(venv_dir, ignore_errors=True)

    def test_install_sh_pins_pyside6_and_contains_the_check(self):
        with open(os.path.join(self._tree(), "install.sh"),
                  encoding="utf-8") as fh:
            script = fh.read()
        self.assertIn("PySide6-Essentials==6.12.0", script)
        self.assertIn(self.INSTALL_CHECK, script)
        self.assertIn("QApplication([])", script)

    def test_qt_start_check_passes_when_qt_works_and_fails_without_it(self):
        # The installer's Qt check: start Qt offscreen. Passes with
        # working Qt, fails with a bogus platform -- so a machine
        # whose Qt cannot start trips the installer's warning.
        start = ("from PySide6 import QtWidgets; "
                 "QtWidgets.QApplication([])")
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        ok = subprocess.run(
            [sys.executable, "-c", start],
            capture_output=True, text=True, timeout=60, env=env)
        self.assertEqual(ok.returncode, 0)
        env["QT_QPA_PLATFORM"] = "no-such-platform"
        bad = subprocess.run(
            [sys.executable, "-c", start],
            capture_output=True, text=True, timeout=60, env=env)
        self.assertNotEqual(bad.returncode, 0)

    def test_run_sh_without_venv_prints_plain_message_and_exits(self):
        # A folder with the app but no .venv: run.sh must say to run
        # the installer and exit non-zero -- never fall back to
        # system python and crash on the missing PySide6.
        work = tempfile.mkdtemp(prefix="duo-no-venv-")
        try:
            for name in ("run.sh", "dual_chat.py"):
                shutil.copy(os.path.join(self._tree(), name),
                            os.path.join(work, name))
            proc = subprocess.run(
                ["bash", "run.sh"], cwd=work,
                capture_output=True, text=True, timeout=60)
            self.assertNotEqual(proc.returncode, 0)
            output = proc.stdout + proc.stderr
            self.assertIn("install.sh", output)
            self.assertNotIn("Traceback", output)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_run_sh_has_no_system_python_fallback(self):
        with open(os.path.join(self._tree(), "run.sh"),
                  encoding="utf-8") as fh:
            script = fh.read()
        self.assertNotIn("exec python3", script)


# ---------------------------------------------------------------------------
# Window finish GUI tests (v0.15, audit T4)
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class WindowFinishGuiTests(unittest.TestCase):
    """The window remembers its size/position and pane split, has an
    icon and the version in its title, and saved pairs can be deleted
    (with a confirmation first)."""
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        # Geometry and the split persist, so clear them before every
        # window: each test starts from the defaults.
        self._clear_window_settings()
        self.client.reset()
        # Pairs go to a temp file so tests never touch the real one.
        self.tmp = tempfile.TemporaryDirectory()
        self.pairs_file = os.path.join(self.tmp.name, "duo-pairs.json")
        self._env = mock.patch.dict(
            os.environ, {"DUAL_CHAT_PAIRS_FILE": self.pairs_file})
        self._env.start()
        from dual_chat import build_window
        self.window = build_window(self.client, logger=TEST_LOGGER)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)

    def tearDown(self):
        self.window.close()
        self._env.stop()
        self.tmp.cleanup()
        self._clear_window_settings()

    def _clear_window_settings(self):
        from PySide6.QtCore import QSettings
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        settings.remove(SETTING_WINDOW_GEOMETRY)
        settings.remove(SETTING_SPLITTER_STATE)
        settings.sync()

    def _wait_for(self, cond, timeout=10.0):
        from PySide6.QtTest import QTest
        deadline = time.time() + timeout
        while time.time() < deadline:
            QTest.qWait(50)
            if cond():
                return True
        return cond()

    def _seed_pairs(self, *names):
        for name in names:
            save_pair(self.pairs_file, name,
                      {"model_a": "fake-a:latest", "model_b": None,
                       "instruction_a": "", "instruction_b": ""})
        self.state["refresh_pair_combo"]()
        self.app.processEvents()

    def test_title_shows_version(self):
        self.assertEqual(self.window.windowTitle(), "DUO " + APP_VERSION)

    def test_window_has_icon(self):
        self.assertFalse(self.window.windowIcon().isNull())

    def test_geometry_and_splitter_round_trip(self):
        from dual_chat import build_window
        self.window.resize(1000, 600)
        self.window.move(40, 30)
        panes = self.state["panes"]
        panes.setSizes([650, 350])
        self.app.processEvents()
        # Capture what the window actually is (the frame offset makes
        # the client rect differ from the move/resize arguments by a
        # couple of pixels): the restore must reproduce exactly this.
        rect_before = self.window.geometry()
        sizes_before = list(panes.sizes())
        self.window.close()
        self.app.processEvents()
        window2 = build_window(self.client, logger=TEST_LOGGER)
        try:
            window2.show()
            state2 = window2._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state2["widgets"]["A"]["picker"].count() == 2))
            self.app.processEvents()
            self.assertEqual(window2.geometry(), rect_before)
            sizes_after = list(state2["panes"].sizes())
            self.assertEqual(len(sizes_after), len(sizes_before))
            for before, after in zip(sizes_before, sizes_after):
                self.assertAlmostEqual(before, after, delta=3)
        finally:
            window2.close()

    def test_offscreen_geometry_is_clamped_on_restore(self):
        from dual_chat import build_window
        # Park the window far off-screen and close it: the close
        # saves that geometry. A fresh window must come back
        # on-screen, title bar reachable.
        self.window.setGeometry(10000, 10000, 1000, 600)
        self.app.processEvents()
        self.window.close()
        self.app.processEvents()
        window2 = build_window(self.client, logger=TEST_LOGGER)
        try:
            window2.show()
            state2 = window2._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state2["widgets"]["A"]["picker"].count() == 2))
            self.app.processEvents()
            avail = self.app.primaryScreen().availableGeometry()
            geom = window2.geometry()
            self.assertTrue(avail.intersects(geom),
                            "restored window must overlap the screen: %s "
                            "vs available %s" % (geom.getRect(),
                                                avail.getRect()))
            self.assertTrue(avail.contains(geom.topLeft()),
                            "title bar must be on screen: %s"
                            % (geom.getRect(),))
            if avail.width() >= window2.minimumWidth():
                self.assertLessEqual(geom.width(), avail.width())
            if avail.height() >= window2.minimumHeight():
                self.assertLessEqual(geom.height(), avail.height())
        finally:
            window2.close()

    def test_pair_delete_disabled_with_nothing_selected(self):
        # No pairs saved, nothing selected: Delete is off.
        self.assertFalse(self.state["pair_delete"].isEnabled())
        self._seed_pairs("one", "two")
        # Still nothing selected after the reload: still off.
        self.assertEqual(self.state["pair_combo"].currentIndex(), -1)
        self.assertFalse(self.state["pair_delete"].isEnabled())
        # Pick one: Delete comes on.
        self.state["pair_combo"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(self.state["pair_delete"].isEnabled())

    def test_pair_delete_removes_pair_from_file_and_picker(self):
        from PySide6.QtWidgets import QMessageBox
        self._seed_pairs("one", "two")
        self.state["pair_combo"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertEqual(self.state["pair_combo"].currentText(), "one")
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.state["pair_delete"].click()
        self.app.processEvents()
        self.assertEqual(set(load_pairs(self.pairs_file)), {"two"})
        self.assertEqual(self.state["pair_combo"].count(), 1)
        self.assertEqual(self.state["pair_combo"].itemText(0), "two")
        # Nothing is selected after a delete -- never auto-pick.
        self.assertEqual(self.state["pair_combo"].currentIndex(), -1)
        self.assertFalse(self.state["pair_delete"].isEnabled())
        # Delete the last one: the picker is empty, still nothing
        # selected, Delete stays off.
        self.state["pair_combo"].setCurrentIndex(0)
        self.app.processEvents()
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.state["pair_delete"].click()
        self.app.processEvents()
        self.assertEqual(load_pairs(self.pairs_file), {})
        self.assertEqual(self.state["pair_combo"].count(), 0)
        self.assertEqual(self.state["pair_combo"].currentIndex(), -1)
        self.assertFalse(self.state["pair_delete"].isEnabled())

    def test_pair_delete_reject_keeps_pair(self):
        from PySide6.QtWidgets import QMessageBox
        self._seed_pairs("one", "two")
        self.state["pair_combo"].setCurrentIndex(0)
        self.app.processEvents()
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            self.state["pair_delete"].click()
        self.app.processEvents()
        self.assertEqual(set(load_pairs(self.pairs_file)),
                         {"one", "two"})
        self.assertEqual(self.state["pair_combo"].count(), 2)

    def test_pair_delete_failure_shows_plain_note(self):
        # v0.18 (audit T7): a failed pairs delete says so plainly
        # instead of crashing, and the old pairs file stays intact.
        from PySide6.QtWidgets import QMessageBox
        self._seed_pairs("one", "two")
        self.state["pair_combo"].setCurrentIndex(0)
        self.app.processEvents()
        with open(self.pairs_file, "rb") as fh:
            before = fh.read()
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes), \
                mock.patch("dual_chat.os.replace",
                           side_effect=OSError("disk full")):
            self.state["pair_delete"].click()
        self.app.processEvents()
        message = self.window.statusBar().currentMessage()
        self.assertTrue(
            message.startswith("Couldn't delete pair 'one'"), message)
        self.assertIn("disk full", message)
        with open(self.pairs_file, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(set(load_pairs(self.pairs_file)),
                         {"one", "two"})
        self.assertEqual(self.state["pair_combo"].count(), 2)
        leftovers = [n for n in os.listdir(self.tmp.name)
                     if n.startswith(".duo-pairs-")]
        self.assertEqual(leftovers, [])
class FrontDoorTests(unittest.TestCase):
    """v0.16 (audit T5): the front door -- CHANGELOG, .gitignore, the
    CI workflow, the .desktop launcher, and the uninstaller."""

    def _tree(self):
        return os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))

    def _read(self, rel):
        with open(os.path.join(self._tree(), rel),
                  encoding="utf-8") as fh:
            return fh.read()

    def test_changelog_covers_every_version(self):
        log = self._read("CHANGELOG.md")
        for version in ["0.%d" % n for n in range(1, 20)]:
            self.assertIn(version, log)

    def test_gitignore_covers_runtime_files(self):
        ignored = self._read(".gitignore")
        for entry in ("__pycache__/", "*.pyc", ".venv/", "duo-debug.log",
                      "duo-pairs.json", "duo.png", "*.zip"):
            self.assertIn(entry, ignored)

    def test_ci_workflow_runs_the_suite_offscreen(self):
        # The workflow must run the suite the way the README says to:
        # Python 3.12, pinned PySide6, unittest discover, offscreen.
        # PyYAML is not in the test venv, so parse with it when it is
        # available and fall back to strict text checks when it isn't.
        text = self._read(".github/workflows/ci.yml")
        try:
            import yaml
        except ImportError:
            yaml = None
        if yaml is not None:
            parsed = yaml.safe_load(text)
            steps = parsed["jobs"]["test"]["steps"]
            commands = [s.get("run", "") for s in steps]
            self.assertTrue(
                any("unittest discover -s tests" in c for c in commands),
                commands)
            self.assertTrue(
                any("PySide6-Essentials==6.12.0" in c for c in commands),
                commands)
            envs = [s.get("env", {}) for s in steps]
            self.assertTrue(
                any(e.get("QT_QPA_PLATFORM") == "offscreen" for e in envs),
                envs)
            self.assertEqual(parsed["jobs"]["test"]["runs-on"],
                             "ubuntu-latest")
        else:
            self.assertNotIn("\t", text)
            self.assertIn("python -m unittest discover -s tests", text)
            self.assertIn("QT_QPA_PLATFORM: offscreen", text)
            self.assertIn("PySide6-Essentials==6.12.0", text)
            self.assertIn("ubuntu-latest", text)
            self.assertIn('python-version: "3.12"', text)

    def _parse_desktop(self, text):
        entries = {}
        section = None
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1]
                entries[section] = {}
                continue
            self.assertIn("=", line)
            key, value = line.split("=", 1)
            entries[section][key.strip()] = value.strip()
        return entries

    def test_desktop_file_parses_and_names_the_app(self):
        # DUO.desktop is a template: install.sh substitutes @APP_DIR@
        # with the real app folder. The template itself must still
        # parse and carry every field the menu needs.
        entries = self._parse_desktop(self._read("DUO.desktop"))
        self.assertIn("Desktop Entry", entries)
        entry = entries["Desktop Entry"]
        self.assertEqual(entry.get("Type"), "Application")
        self.assertEqual(entry.get("Name"), "DUO")
        self.assertIn("run.sh", entry.get("Exec", ""))
        self.assertIn("@APP_DIR@", entry.get("Exec", ""))
        self.assertIn("duo.png", entry.get("Icon", ""))
        self.assertIn("Utility", entry.get("Categories", ""))
        self.assertEqual(entry.get("Terminal"), "false")
        self.assertIn("Comment", entry)

    def test_installed_desktop_file_validates_if_validator_present(self):
        # The file install.sh writes -- template with @APP_DIR@
        # substituted -- must pass desktop-file-validate where the
        # validator exists.
        validator = shutil.which("desktop-file-validate")
        if validator is None:
            self.skipTest("desktop-file-validate not installed")
        work = tempfile.mkdtemp(prefix="duo-desktop-validate-")
        try:
            installed = os.path.join(work, "DUO.desktop")
            text = self._read("DUO.desktop").replace("@APP_DIR@", work)
            with open(installed, "w", encoding="utf-8") as fh:
                fh.write(text)
            proc = subprocess.run(
                [validator, installed],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(proc.returncode, 0,
                             proc.stdout + proc.stderr)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def test_install_sh_installs_the_desktop_entry(self):
        script = self._read("install.sh")
        self.assertIn("install_desktop_entry", script)
        self.assertIn(".local/share/applications", script)
        self.assertIn("DUO.desktop", script)
        self.assertIn("duo.png", script)
        # The icon step draws from the app's own window icon, and the
        # menu step must never fail the install.
        self.assertIn("make_app_icon", script)
        self.assertIn("could not add the menu entry", script)

    def test_readme_notes_license_pending_and_clone_modes(self):
        readme = self._read("README.md")
        self.assertIn("License: not decided yet", readme)
        self.assertIn("carries the executable bits", readme)
        self.assertIn("uninstall.sh", readme)

    def _uninstall_setup(self):
        # A fake install: the app folder plus a fake HOME holding a
        # DUO menu entry, another app's menu entry, and a sentinel
        # file that must survive the uninstall.
        work = tempfile.mkdtemp(prefix="duo-uninstall-")
        home = tempfile.mkdtemp(prefix="duo-uninstall-home-")
        shutil.copy(os.path.join(self._tree(), "uninstall.sh"),
                    os.path.join(work, "uninstall.sh"))
        with open(os.path.join(work, "dual_chat.py"), "w") as fh:
            fh.write("# app file -- must survive\n")
        os.mkdir(os.path.join(work, ".venv"))
        with open(os.path.join(work, ".venv", "pyvenv.cfg"), "w") as fh:
            fh.write("home = /usr\n")
        for name in ("duo-pairs.json", "duo-debug.log", "duo.png"):
            with open(os.path.join(work, name), "w") as fh:
                fh.write("generated\n")
        apps = os.path.join(home, ".local", "share", "applications")
        os.makedirs(apps)
        with open(os.path.join(apps, "DUO.desktop"), "w") as fh:
            fh.write("[Desktop Entry]\nName=DUO\n")
        with open(os.path.join(apps, "other.desktop"), "w") as fh:
            fh.write("[Desktop Entry]\nName=Other\n")
        with open(os.path.join(home, "keep-me.txt"), "w") as fh:
            fh.write("sentinel\n")
        return work, home

    def _run_uninstall(self, work, home, answer):
        env = dict(os.environ)
        env["HOME"] = home
        return subprocess.run(
            ["bash", "uninstall.sh"], cwd=work, env=env,
            input=answer + "\n",
            capture_output=True, text=True, timeout=60)

    def test_uninstall_yes_removes_only_duo_files(self):
        work, home = self._uninstall_setup()
        try:
            proc = self._run_uninstall(work, home, "y")
            self.assertEqual(proc.returncode, 0,
                             proc.stdout + proc.stderr)
            apps = os.path.join(home, ".local", "share", "applications")
            self.assertFalse(os.path.exists(
                os.path.join(apps, "DUO.desktop")))
            for name in (".venv", "duo-pairs.json", "duo-debug.log",
                         "duo.png"):
                self.assertFalse(os.path.exists(
                    os.path.join(work, name)), name)
            # Everything else survives: the app folder's own files,
            # the other app's menu entry, and the sentinel.
            self.assertTrue(os.path.exists(
                os.path.join(work, "uninstall.sh")))
            self.assertTrue(os.path.exists(
                os.path.join(work, "dual_chat.py")))
            self.assertTrue(os.path.exists(
                os.path.join(apps, "other.desktop")))
            self.assertTrue(os.path.exists(
                os.path.join(home, "keep-me.txt")))
            self.assertIn("Removed:", proc.stdout)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)

    def test_uninstall_no_keeps_everything(self):
        work, home = self._uninstall_setup()
        try:
            proc = self._run_uninstall(work, home, "n")
            self.assertEqual(proc.returncode, 0,
                             proc.stdout + proc.stderr)
            apps = os.path.join(home, ".local", "share", "applications")
            self.assertTrue(os.path.exists(
                os.path.join(apps, "DUO.desktop")))
            for name in (".venv", "duo-pairs.json", "duo-debug.log",
                         "duo.png"):
                self.assertTrue(os.path.exists(
                    os.path.join(work, name)), name)
            self.assertIn("Nothing removed.", proc.stdout)
        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(home, ignore_errors=True)


# ---------------------------------------------------------------------------
# Atomic writes (v0.18, audit T7)
# ---------------------------------------------------------------------------

class AtomicWriteTests(unittest.TestCase):
    """v0.18 (audit T7): every file DUO writes -- pairs, transcripts,
    conversations -- goes through write_text_atomic: content to a
    temp file in the same folder, then a rename over the target, so
    a crash mid-save can never leave a half-written file behind."""

    def test_round_trip_writes_exact_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.txt")
            write_text_atomic(path, "hello\nworld\n")
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "hello\nworld\n")
            self.assertEqual(os.listdir(tmp), ["out.txt"])

    def test_failed_rename_leaves_old_file_intact(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("the old file\n")
            with mock.patch("dual_chat.os.replace",
                            side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_text_atomic(path, "the new file\n")
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "the old file\n")
            self.assertEqual(os.listdir(tmp), ["out.txt"])

    def test_failed_write_leaves_no_temp_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "out.txt")
            with mock.patch("dual_chat.os.replace",
                            side_effect=OSError("disk full")):
                with self.assertRaises(OSError):
                    write_text_atomic(path, "never lands\n")
            self.assertEqual(os.listdir(tmp), [])

    def test_missing_folder_raises_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "no-such-dir", "out.txt")
            with self.assertRaises(OSError):
                write_text_atomic(path, "never lands\n")
            self.assertEqual(os.listdir(tmp), [])


class ConversationFileTests(unittest.TestCase):
    """v0.17 (audit T6): the saved-conversation file format -- both
    sides' histories with labels, both models, both instruction
    fields, and the interleaved full-transcript log, written
    atomically and validated strictly on load."""

    def _make_convo(self):
        convo = Conversation()
        a = convo.sides["A"]
        a.model = "fake-a:latest"
        a.instruction = "Be brief."
        a.add_user("hello", label="You")
        a.add_assistant("Reply from A.", label="fake-a:latest")
        a.add_user("alpha one", label="You [relay from Model B]")
        b = convo.sides["B"]
        b.model = "fake-b:latest"
        b.instruction = ""
        # A message carrying an attached file: the full content
        # holds the file, the display text is what the window shows.
        b.add_user("File: notes.txt\nline one\n\nread this",
                   label="You",
                   display="read this\n(Attached: notes.txt)")
        b.add_assistant("Reply from B.", label="fake-b:latest")
        log = [("A", "You", "hello"),
               ("A", "fake-a:latest", "Reply from A."),
               ("B", "You", "read this\n(Attached: notes.txt)"),
               ("B", "fake-b:latest", "Reply from B."),
               ("A", "You [relay from Model B]", "alpha one")]
        return convo, log

    def test_save_writes_valid_json(self):
        convo, log = self._make_convo()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "saved.json")
            save_conversation_file(path, convo, log)
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        self.assertEqual(data["version"], CONVERSATION_FORMAT_VERSION)
        a = data["sides"]["A"]
        self.assertEqual(a["model"], "fake-a:latest")
        self.assertEqual(a["instruction"], "Be brief.")
        self.assertEqual(
            [e["role"] for e in a["entries"]],
            ["user", "assistant", "user"])
        self.assertEqual(
            [e["label"] for e in a["entries"]],
            ["You", "fake-a:latest", "You [relay from Model B]"])
        self.assertEqual(a["entries"][2]["content"], "alpha one")
        b = data["sides"]["B"]
        self.assertEqual(b["model"], "fake-b:latest")
        self.assertEqual(b["instruction"], "")
        self.assertEqual(b["entries"][0]["content"],
                         "File: notes.txt\nline one\n\nread this")
        self.assertEqual(b["entries"][0]["display"],
                         "read this\n(Attached: notes.txt)")
        self.assertIsNone(b["entries"][1]["display"])
        self.assertEqual(data["log"], [list(item) for item in log])

    def test_round_trip_restores_everything(self):
        convo, log = self._make_convo()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "saved.json")
            save_conversation_file(path, convo, log)
            restored, restored_log = load_conversation_file(path)
        for side in ("A", "B"):
            self.assertEqual(restored.sides[side].history,
                             convo.sides[side].history)
            self.assertEqual(restored.sides[side].labels,
                             convo.sides[side].labels)
            self.assertEqual(restored.sides[side].display,
                             convo.sides[side].display)
            self.assertEqual(restored.sides[side].instruction,
                             convo.sides[side].instruction)
            self.assertEqual(restored.sides[side].model,
                             convo.sides[side].model)
        self.assertEqual(restored_log, log)

    def test_default_filename(self):
        self.assertEqual(
            default_conversation_filename(datetime.date(2026, 11, 12)),
            "duo-conversation-2026-11-12.json")

    def test_corrupt_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            with self.assertRaises(ConversationError):
                load_conversation_file(path)

    def test_wrong_version_raises(self):
        convo, log = self._make_convo()
        data = conversation_to_data(convo, log)
        data["version"] = 99
        with self.assertRaises(ConversationError):
            data_to_conversation(data)

    def test_missing_keys_raise(self):
        convo, log = self._make_convo()
        good = conversation_to_data(convo, log)

        def broken(mutate):
            data = copy.deepcopy(good)
            mutate(data)
            return data

        cases = [
            lambda d: d.pop("sides"),
            lambda d: d.pop("log"),
            lambda d: d["sides"].pop("B"),
            lambda d: d["sides"]["A"].pop("entries"),
            lambda d: d["sides"]["A"]["entries"][0].pop("content"),
            lambda d: d["sides"]["A"]["entries"][0].update(role="system"),
            lambda d: d["log"].append(["C", "You", "nope"]),
        ]
        for mutate in cases:
            with self.assertRaises(ConversationError):
                data_to_conversation(broken(mutate))

    def test_atomic_write_leaves_no_temp_behind(self):
        convo, log = self._make_convo()
        with tempfile.TemporaryDirectory() as tmp:
            before = set(os.listdir(tmp))
            path = os.path.join(tmp, "saved.json")
            save_conversation_file(path, convo, log)
            after = set(os.listdir(tmp))
            self.assertEqual(after - before, {"saved.json"})
            self.assertFalse([n for n in after
                              if n.startswith(".duo-convo-")])

    def test_save_overwrites_cleanly(self):
        convo, log = self._make_convo()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "saved.json")
            save_conversation_file(path, convo, log)
            convo.sides["A"].add_user("second save", label="You")
            save_conversation_file(path, convo, log)
            restored, _ = load_conversation_file(path)
            self.assertEqual(
                restored.sides["A"].history[-1]["content"],
                "second save")


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class ConversationGuiTests(unittest.TestCase):
    """v0.17 (audit T6): Save conversation / Open conversation in
    the window -- round-trip through the buttons, the missing-model
    note, the replace confirmation, and bad files that change
    nothing."""
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
        self.window = build_window(self.client, logger=TEST_LOGGER)
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

    def _send_both_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_both"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and len(self.state["convo"].sides["B"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _send_to_a_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_a"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _save_dialog(self, path):
        """Patch the save dialog to return ``path`` ("" cancels)."""
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getSaveFileName",
            return_value=(path, "DUO conversation files (*.json)"))

    def _open_dialog(self, path):
        """Patch the open dialog to return ``path`` ("" cancels)."""
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getOpenFileName",
            return_value=(path, "DUO conversation files (*.json)"))

    def _answer(self, button):
        from PySide6.QtWidgets import QMessageBox
        return mock.patch.object(QMessageBox, "question",
                                 return_value=button)

    def _read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _snapshot(self):
        convo = self.state["convo"]
        return {
            side: (copy.deepcopy(convo.sides[side].history),
                   list(convo.sides[side].labels),
                   list(convo.sides[side].display),
                   convo.sides[side].instruction,
                   convo.sides[side].model)
            for side in ("A", "B")}

    def _assert_snapshot(self, snap):
        convo = self.state["convo"]
        for side in ("A", "B"):
            history, labels, display, instruction, model = snap[side]
            self.assertEqual(convo.sides[side].history, history)
            self.assertEqual(convo.sides[side].labels, labels)
            self.assertEqual(convo.sides[side].display, display)
            self.assertEqual(convo.sides[side].instruction, instruction)
            self.assertEqual(convo.sides[side].model, model)

    def test_buttons_enabled_when_idle(self):
        self.assertTrue(self.state["save_convo"].isEnabled())
        self.assertTrue(self.state["open_convo"].isEnabled())
        self.assertEqual(self.state["save_convo"].text(),
                         "Save conversation")
        self.assertEqual(self.state["open_convo"].text(),
                         "Open conversation")

    def test_save_then_open_restores_everything(self):
        from PySide6.QtWidgets import QMessageBox
        self.state["widgets"]["A"]["instr_edit"].setPlainText("Be brief.")
        self.app.processEvents()
        self.assertTrue(self._send_both_and_wait("hello both"))
        snap = self._snapshot()
        full_before = format_full_transcript(
            self.state["state"]["full_log"])
        path = os.path.join(self.tmp.name, "saved.json")
        with self._save_dialog(path):
            self.state["save_convo"].click()
        self.assertEqual(self.window.statusBar().currentMessage(),
                         "Conversation saved to %s" % path)
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["version"],
                             CONVERSATION_FORMAT_VERSION)
        # Clear both panes, then open the file back.
        self.state["widgets"]["A"]["clear"].click()
        self.state["widgets"]["B"]["clear"].click()
        self.app.processEvents()
        self.assertEqual(self.state["convo"].sides["A"].history, [])
        self.assertEqual(self.state["convo"].sides["B"].history, [])
        self.assertEqual(self.state["state"]["full_log"], [])
        with self._open_dialog(path), self._answer(
                QMessageBox.StandardButton.Yes):
            self.state["open_convo"].click()
        self.app.processEvents()
        self._assert_snapshot(snap)
        self.assertEqual(
            self.state["widgets"]["A"]["instr_edit"].toPlainText(),
            "Be brief.")
        # The panes show the loaded conversation, and the full
        # transcript is the same interleaved log as before.
        self.assertIn("hello both",
                      self.state["widgets"]["A"]["transcript"].toPlainText())
        self.assertIn("Reply from fake-b:latest.",
                      self.state["widgets"]["B"]["transcript"].toPlainText())
        self.assertEqual(
            format_full_transcript(self.state["state"]["full_log"]),
            full_before)

    def test_open_missing_model_leaves_picker_blank(self):
        convo = Conversation()
        convo.sides["A"].model = "gone-model:latest"
        convo.sides["A"].add_user("hello", label="You")
        convo.sides["B"].model = "fake-b:latest"
        convo.sides["B"].add_user("hello", label="You")
        path = os.path.join(self.tmp.name, "gone.json")
        save_conversation_file(path, convo, [("A", "You", "hello"),
                                             ("B", "You", "hello")])
        with self._open_dialog(path):
            self.state["open_convo"].click()
        self.app.processEvents()
        # A stays unpicked; B loads fine.
        self.assertEqual(
            self.state["widgets"]["A"]["picker"].currentIndex(), -1)
        self.assertIsNone(self.state["convo"].sides["A"].model)
        self.assertEqual(self.state["convo"].sides["B"].model,
                         "fake-b:latest")
        self.assertEqual(self.state["convo"].sides["A"].history,
                         [{"role": "user", "content": "hello"}])
        message = self.window.statusBar().currentMessage()
        self.assertIn("not installed", message)
        self.assertIn("gone-model:latest", message)

    def test_open_corrupt_file_changes_nothing(self):
        self.assertTrue(self._send_to_a_and_wait("hello"))
        snap = self._snapshot()
        path = os.path.join(self.tmp.name, "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        with self._open_dialog(path):
            self.state["open_convo"].click()
        self.app.processEvents()
        self._assert_snapshot(snap)
        self.assertIn("Couldn't open that file",
                      self.window.statusBar().currentMessage())

    def test_open_reject_keeps_current_conversation(self):
        from PySide6.QtWidgets import QMessageBox
        self.assertTrue(self._send_to_a_and_wait("hello"))
        snap = self._snapshot()
        convo = Conversation()
        convo.sides["A"].model = "fake-a:latest"
        convo.sides["A"].add_user("other chat", label="You")
        path = os.path.join(self.tmp.name, "other.json")
        save_conversation_file(path, convo, [("A", "You", "other chat")])
        with self._open_dialog(path), self._answer(
                QMessageBox.StandardButton.No):
            self.state["open_convo"].click()
        self.app.processEvents()
        self._assert_snapshot(snap)

    def test_open_empty_panes_asks_nothing(self):
        from PySide6.QtWidgets import QMessageBox
        convo = Conversation()
        convo.sides["A"].model = "fake-a:latest"
        convo.sides["A"].add_user("fresh load", label="You")
        path = os.path.join(self.tmp.name, "fresh.json")
        save_conversation_file(path, convo, [("A", "You", "fresh load")])
        with self._open_dialog(path), mock.patch.object(
                QMessageBox, "question",
                side_effect=AssertionError("must not ask")):
            self.state["open_convo"].click()
        self.app.processEvents()
        self.assertEqual(
            self.state["convo"].sides["A"].history,
            [{"role": "user", "content": "fresh load"}])

    def test_save_cancel_writes_nothing(self):
        self.assertTrue(self._send_to_a_and_wait("hello"))
        before = set(os.listdir(self.tmp.name))
        with self._save_dialog(""):
            self.state["save_convo"].click()
        self.assertEqual(set(os.listdir(self.tmp.name)), before)


# ---------------------------------------------------------------------------
# Temperature controls (v0.19, audit T8)
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class TemperatureGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        # Temperatures are persisted per side, so clear both before
        # every window: each test starts from the default (0.7).
        self._clear_temperatures()
        from dual_chat import build_window
        self.client.reset()
        self.window = build_window(self.client, logger=TEST_LOGGER)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self._pick_models()

    def tearDown(self):
        self.window.close()
        self._clear_temperatures()

    def _clear_temperatures(self):
        from PySide6.QtCore import QSettings
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        settings.remove(SETTING_TEMPERATURE % "A")
        settings.remove(SETTING_TEMPERATURE % "B")
        settings.sync()

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

    def _temp(self, side):
        return self.state["widgets"][side]["temperature"]

    def test_controls_present_and_default(self):
        for side in ("A", "B"):
            spin = self._temp(side)
            self.assertTrue(spin.isVisible())
            self.assertEqual(spin.value(), TEMPERATURE_DEFAULT)
            self.assertEqual(spin.minimum(), TEMPERATURE_MIN)
            self.assertEqual(spin.maximum(), TEMPERATURE_MAX)
            self.assertEqual(spin.singleStep(), 0.1)
            self.assertEqual(spin.decimals(), 1)
            self.assertIn("0.7 is Ollama's default", spin.toolTip())

    def test_temperature_clamps_to_range(self):
        self._temp("A").setValue(5.0)
        self.assertEqual(self._temp("A").value(), TEMPERATURE_MAX)
        self._temp("A").setValue(-1.0)
        self.assertEqual(self._temp("A").value(), TEMPERATURE_MIN)

    def test_temperature_persists_across_restart(self):
        from dual_chat import build_window
        self._temp("A").setValue(1.3)
        self._temp("B").setValue(0.2)
        self.app.processEvents()
        self.window.close()
        self.app.processEvents()
        window2 = build_window(self.client, logger=TEST_LOGGER)
        try:
            window2.show()
            state2 = window2._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state2["widgets"]["A"]["picker"].count() == 2))
            self.assertEqual(
                state2["widgets"]["A"]["temperature"].value(), 1.3)
            self.assertEqual(
                state2["widgets"]["B"]["temperature"].value(), 0.2)
        finally:
            window2.close()

    def test_send_to_both_transmits_each_sides_temperature(self):
        self._temp("A").setValue(0.3)
        self._temp("B").setValue(1.4)
        self.app.processEvents()
        self.client.options_seen.clear()
        self.state["composer"].setPlainText("Hello, both of you.")
        self.state["send_both"].click()
        ok = self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].history) == 2
            and len(self.state["convo"].sides["B"].history) == 2),
            timeout=15)
        self.assertTrue(ok, "send to both should land in both panes")
        self.assertEqual(
            self.client.options_seen,
            [("fake-a:latest", {"temperature": 0.3}),
             ("fake-b:latest", {"temperature": 1.4})])

    def test_relay_hops_transmit_each_sides_temperature(self):
        self._temp("A").setValue(0.3)
        self._temp("B").setValue(1.4)
        self.app.processEvents()
        self.client.options_seen.clear()
        self.state["start_picker"].setCurrentIndex(0)  # A starts
        self.state["cap_spin"].setValue(2)
        self.state["mode_picker"].setCurrentIndex(0)  # Blind
        self.state["composer"].setPlainText("Talk to each other.")
        self.app.processEvents()
        self.state["relay_btn"].click()
        ok = self._wait_for(
            lambda: self.state["relay_status"].text().startswith(
                "Relay stopped"),
            timeout=20)
        self.assertTrue(ok, "relay should run and stop: %s"
                        % self.state["relay_status"].text())
        # Turn 1 is A's (the seed), turn 2 is B's (A's reply).
        self.assertEqual(
            self.client.options_seen,
            [("fake-a:latest", {"temperature": 0.3}),
             ("fake-b:latest", {"temperature": 1.4})])


if __name__ == "__main__":
    unittest.main()
