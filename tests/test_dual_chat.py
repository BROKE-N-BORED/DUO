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
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dual_chat import (  # noqa: E402
    AGREEMENT_PHRASES, APP_VERSION, AWARE_TEMPLATE, CHAT_MAX_ATTEMPTS,
    CHAT_RETRY_DELAYS, CHAT_TIMEOUT, COMPARE_PREFIX,
    CONVERSATION_FORMAT_VERSION, DECK_PAGE_HTML,
    DECK_PORT, ERROR_STATE_HOLD_MS, FULL_LOG_MAX_ENTRIES,
    FULL_LOG_TRIMMED_NOTE,
    MAX_ATTACHMENTS,
    MAX_ATTACHMENT_BYTES, MSG_EMPTY, MSG_MODEL_FAILED, MSG_OLLAMA_DOWN,
    MSG_OLLAMA_NOT_INSTALLED, MSG_OLLAMA_SLOW, MSG_OLLAMA_START_FAILED,
    MSG_PHONE_BUSY, MSG_PHONE_RELAY, MSG_PHONE_TARGET,
    MSG_UPDATE_AVAILABLE, MSG_UPDATE_FAILED, MSG_UPDATE_LATEST,
    SETTINGS_APP, SETTINGS_ORG, SETTING_DIRECTOR_COLLAPSED,
    SETTING_ENTER_TO_SEND,
    SETTING_SPLITTER_STATE, SETTING_TEMPERATURE,
    SETTING_WINDOW_GEOMETRY,
    SIDE_STATES,
    TASK_CONTINUE_TEMPLATE, TASK_FOLLOW_UPS, TASK_REVIEW_TEMPLATE,
    TASK_REVISE_TEMPLATE, TASK_ROUNDS_DEFAULT, TASK_ROUNDS_MAX,
    TASK_ROUNDS_MIN,
    TEMPERATURE_DEFAULT, TEMPERATURE_MAX, TEMPERATURE_MIN,
    UPDATE_CHECK_TIMEOUT, UPDATE_CHECK_URL, UPDATE_CHECK_URL_ENV,
    AttachmentError, Cancelled,
    ChatMessage, ChatSide, Conversation, ConversationError, DeckServer,
    HISTORY_BUDGET_CHARS,
    NoModelSelected,
    OllamaClient, OllamaError, OllamaServerManager, RelaySession,
    SUMMARY_KEEP_LAST, SUMMARY_LABEL, SUMMARIZE_PROMPT, SerialRunner,
    TaskSession, TranscriptError, append_full_log_entry, attachment_tag,
    build_attached_message,
    build_deck_state, check_for_update, compute_turn_stats,
    conversation_to_data, cpu_percent_between,
    data_to_conversation, default_both_filename,
    default_conversation_filename, default_debug_filename,
    default_full_filename, default_side_filename, delete_pair,
    delete_preset,
    format_both_transcript,
    format_full_transcript, format_ram, format_session_line,
    format_side_transcript,
    format_stats_line, format_system_meter, get_lan_ip,
    is_valid_preset, load_attachment,
    load_conversation_file, load_pairs, load_presets,
    load_side_transcript_file,
    make_debug_logger,
    markdown_to_html,
    pairs_path, parse_cpu_times, parse_meminfo,
    parse_ollama_list_output, parse_ps_payload,
    parse_side_transcript, parse_stop_phrases, parse_tags_payload,
    parse_version, phrase_matches,
    presets_file_corrupt, presets_path,
    read_system_meter,
    render_qr_pixmap,
    sanitize_filename_part,
    save_conversation_file, save_pair, save_preset,
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
                            "prompt_eval_count": 128,
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
        self.assertEqual(len(self.convo.sides["A"].messages), 2)
        self.assertEqual(self.convo.sides["B"].messages, [])

    def test_clear_only_clears_one_side(self):
        self.convo.sides["A"].add_user("x")
        self.convo.sides["B"].add_user("y")
        self.convo.sides["A"].clear()
        self.assertEqual(self.convo.sides["A"].messages, [])
        self.assertEqual(len(self.convo.sides["B"].messages), 1)

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
        self.assertEqual(side.messages, [])


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

    def test_rounds_default_one(self):
        # The spinbox default is 1. With no follow-up that is one
        # round; with a follow-up the chain floors at two rounds
        # (worker + one follow-up) -- the v0.21 behaviour.
        session = TaskSession("A", "nothing")
        self.assertEqual(session.rounds, 1)
        self.assertEqual(session.rounds, TASK_ROUNDS_DEFAULT)
        self.assertEqual(session.rounds_done, 0)
        self.assertEqual(TaskSession("A", "review").rounds, 2)
        self.assertEqual(TaskSession("A", "continue").rounds, 2)

    def test_rounds_clamped(self):
        self.assertEqual(TaskSession("A", "continue", 99).rounds,
                         TASK_ROUNDS_MAX)
        # Below the floor with a follow-up lands on 2, not 1: the
        # follow-up round always happens.
        self.assertEqual(TaskSession("A", "continue", 0).rounds, 2)
        self.assertEqual(TaskSession("A", "continue", -3).rounds, 2)
        self.assertEqual(TaskSession("A", "continue", 4).rounds, 4)
        # Without a follow-up the clamp still lands on 1.
        self.assertEqual(TaskSession("A", "nothing", 0).rounds, 1)

    def test_nothing_forces_one_round(self):
        # No follow-up, no chain -- whatever was asked.
        self.assertEqual(TaskSession("A", "nothing", 5).rounds, 1)

    def test_follow_up_floors_rounds_at_two(self):
        # Rounds 1 with Review/Continue is the v0.21 task: the worker
        # plus one follow-up round, never the worker alone.
        self.assertEqual(TaskSession("A", "review", 1).rounds, 2)
        self.assertEqual(TaskSession("A", "continue", 1).rounds, 2)
        self.assertEqual(TaskSession("A", "review", 2).rounds, 2)

    def test_next_side_alternates(self):
        session = TaskSession("A", "continue", 4)
        self.assertEqual(session.next_side, "A")  # round 1: worker
        session.rounds_done = 1
        self.assertEqual(session.next_side, "B")  # round 2: other
        session.rounds_done = 2
        self.assertEqual(session.next_side, "A")  # round 3: worker
        session.rounds_done = 3
        self.assertEqual(session.next_side, "B")  # round 4: other
        # Worker B mirrors it.
        other = TaskSession("B", "continue", 3)
        self.assertEqual(other.next_side, "B")
        other.rounds_done = 1
        self.assertEqual(other.next_side, "A")

    def test_frame_next_continue_always_continues(self):
        session = TaskSession("A", "continue", 4)
        for done in (1, 2, 3):
            session.rounds_done = done
            self.assertEqual(session.frame_next("Work so far."),
                             TASK_CONTINUE_TEMPLATE % "Work so far.")

    def test_frame_next_review_then_revise(self):
        session = TaskSession("A", "review", 4)
        # Round 2: the other side reviews.
        session.rounds_done = 1
        self.assertEqual(session.frame_next("The draft is done."),
                         TASK_REVIEW_TEMPLATE % "The draft is done.")
        # Round 3: the worker revises from the review.
        session.rounds_done = 2
        self.assertEqual(session.frame_next("Needs work."),
                         TASK_REVISE_TEMPLATE % "Needs work.")
        # Round 4: a review turn again -- the alternation holds.
        session.rounds_done = 3
        self.assertEqual(session.frame_next("Revised draft."),
                         TASK_REVIEW_TEMPLATE % "Revised draft.")

    def test_revise_template_exact(self):
        self.assertEqual(
            TASK_REVISE_TEMPLATE % "Needs work.",
            "Here is a review of your work. Revise your work based "
            "on it:\n\nNeeds work.")

    def test_frame_next_nothing_is_none(self):
        self.assertIsNone(
            TaskSession("A", "nothing", 1).frame_next("anything"))

    def test_handoff_note_next(self):
        session = TaskSession("A", "continue", 3)
        session.rounds_done = 1  # handing B's result back to A
        self.assertEqual(session.handoff_note_next("B"),
                         "Task result from Model B, to continue.")
        review = TaskSession("A", "review", 4)
        review.rounds_done = 1  # handing A's result to B to review
        self.assertEqual(review.handoff_note_next("A"),
                         "Task result from Model A, for review.")
        review.rounds_done = 2  # handing B's review to A to revise
        self.assertEqual(review.handoff_note_next("B"),
                         "Review from Model B, to revise.")


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
        self.assertEqual(seen["prompt_eval_count"], 128)
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
                       "prompt_eval_count": 100,
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
            and len(self.state["convo"].sides["A"].messages) == 2
            and len(self.state["convo"].sides["B"].messages) == 2),
            timeout=15)
        self.assertTrue(ok, "both panes should show their replies")
        # One at a time, A first.
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest"])
        # Each side's history holds its own exchange only.
        convo = self.state["convo"]
        self.assertEqual(
            [m.content for m in convo.sides["A"].messages],
            ["Hello, both of you.", "Reply from fake-a:latest."])
        self.assertEqual(
            [m.content for m in convo.sides["B"].messages],
            ["Hello, both of you.", "Reply from fake-b:latest."])

    def test_send_to_a_only(self):
        self._pick_models()
        self.state["composer"].setPlainText("Just for A.")
        self.state["send_a"].click()
        ok = self._wait_for(lambda: (
            "Reply from fake-a:latest." in
            self.state["widgets"]["A"]["transcript"].toPlainText()
            and len(self.state["convo"].sides["A"].messages) == 2),
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
            and len(self.state["convo"].sides["A"].messages) == 2),
            timeout=15))
        self.state["widgets"]["A"]["clear"].click()
        self.assertEqual(self.state["convo"].sides["A"].messages, [])
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
            for message in convo.sides[side].messages:
                self.assertNotIn("[relay", message.content)
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
        return [m.content for m in side.messages]

    def test_labels_default(self):
        side = ChatSide("A")
        side.model = "fake-a:latest"
        side.add_user("hi")
        side.add_assistant("hello")
        self.assertEqual([m.label for m in side.messages], ["You", "fake-a:latest"])
        side2 = ChatSide("B")
        side2.add_assistant("hello")
        self.assertEqual([m.label for m in side2.messages], ["Assistant"])

    def test_labels_move_with_entries(self):
        side = self._side_with_four()
        side.move_up(2)
        self.assertEqual(self._contents(side),
                         ["first", "second", "alpha one", "alpha two"])
        self.assertEqual([m.label for m in side.messages],
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
        self.assertEqual([m.label for m in side.messages], ["You", "You", "fake-a:latest"])
        side.delete_at(99)  # out of range: no-op
        side.delete_at(-1)
        self.assertEqual(len(side.messages), 3)

    def test_clear_clears_labels_too(self):
        side = self._side_with_four()
        side.clear()
        self.assertEqual(side.messages, [])
        self.assertEqual([m.label for m in side.messages], [])

    def test_messages_for_model_without_instruction(self):
        side = self._side_with_four()
        expected = [m.for_model() for m in side.messages]
        self.assertEqual(side.messages_for_model(), expected)
        # A fresh list of fresh dicts each call: mutating what comes
        # back must never touch the side's messages.
        sent = side.messages_for_model()
        sent.append({"role": "user", "content": "injected"})
        sent[0]["content"] = "tampered"
        self.assertEqual(len(side.messages), 4)
        self.assertEqual(side.messages[0].content, "first")

    def test_messages_for_model_with_instruction(self):
        side = self._side_with_four()
        side.instruction = "Be brief."
        self.assertEqual(side.messages_for_model(), [
            {"role": "system", "content": "Be brief."},
        ] + [m.for_model() for m in side.messages])

    def test_messages_for_model_whitespace_instruction_is_none(self):
        side = self._side_with_four()
        side.instruction = "   \n  "
        self.assertEqual(side.messages_for_model(),
                         [m.for_model() for m in side.messages])


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
            [m.content for m in side.messages],
            ["Earlier in this conversation: They talked about fruit.",
             "second", "alpha two", "third", "alpha three"])
        self.assertEqual([m.label for m in side.messages], [
            SUMMARY_LABEL, "You", "fake-a:latest", "You", "fake-a:latest"])
        self.assertEqual(side.messages[0].role, "user")
        self.assertEqual(SUMMARY_KEEP_LAST, 4)

    def test_summarize_short_history_is_noop(self):
        side = ChatSide("A")
        side.add_user("hi")
        side.add_assistant("hello")
        self.assertFalse(side.summarize("whatever"))
        self.assertEqual(len(side.messages), 2)
        # Exactly 4 entries: nothing older to fold.
        side.add_user("again")
        side.add_assistant("yep")
        self.assertFalse(side.summarize("whatever"))
        self.assertEqual(len(side.messages), 4)

    def test_summarize_five_entries_folds_one(self):
        side = self._side_with_six()
        side.delete_at(0)
        self.assertEqual(len(side.messages), 5)
        self.assertTrue(side.summarize("Summary."))
        self.assertEqual(
            [m.content for m in side.messages],
            ["Earlier in this conversation: Summary.",
             "second", "alpha two", "third", "alpha three"])

    def test_summarize_empty_summary_is_noop(self):
        side = self._side_with_six()
        self.assertFalse(side.summarize("   "))
        self.assertEqual(len(side.messages), 6)

    def test_second_summarize_folds_the_first(self):
        side = self._side_with_six()
        side.summarize("First summary.")
        side.add_user("fourth")
        side.add_assistant("alpha four")
        self.assertEqual(len(side.messages), 7)
        self.assertTrue(side.summarize("Second summary."))
        self.assertEqual(
            [m.content for m in side.messages],
            ["Earlier in this conversation: Second summary.",
             "third", "alpha three", "fourth", "alpha four"])
        # One summary entry, not two: the old one was folded in.
        self.assertEqual([m.label for m in side.messages].count(SUMMARY_LABEL), 1)

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
            "prompt_eval_count": 128,
            "ttft_s": 0.5, "total_s": 2.5})
        self.assertEqual(stats, {"tok_per_sec": 20.0,
                                 "eval_count": 40,
                                 "prompt_eval_count": 128,
                                 "ttft_s": 0.5, "total_s": 2.5})

    def test_compute_missing_fields_are_none(self):
        self.assertEqual(compute_turn_stats({}),
                         {"tok_per_sec": None, "eval_count": None,
                          "prompt_eval_count": None, "ttft_s": None,
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
                         "-- tok/s · -- out / -- in · first token -- · "
                         "turn -- · RAM --")
        self.assertEqual(format_stats_line({
            "tok_per_sec": 20.0, "eval_count": 40,
            "prompt_eval_count": 128, "ttft_s": 0.5, "total_s": 2.5,
            "ram": "1.9 GB in RAM"}),
            "20.0 tok/s · 40 out / 128 in · first token 0.50s · "
            "turn 2.5s · 1.9 GB in RAM")
        self.assertEqual(format_stats_line({
            "tok_per_sec": None, "eval_count": None,
            "prompt_eval_count": None, "ttft_s": None, "total_s": None,
            "ram": "not loaded"}),
            "tok/s n/a · tokens n/a · first token n/a · turn n/a · "
            "not loaded")
        self.assertEqual(format_stats_line({
            "tok_per_sec": 12.345, "eval_count": 7,
            "prompt_eval_count": 90, "ttft_s": 1.0, "total_s": 10.0,
            "ram": None}),
            "12.3 tok/s · 7 out / 90 in · first token 1.00s · "
            "turn 10.0s · RAM n/a")

    def test_format_stats_line_partial_counts(self):
        # Only one count reported: show the half Ollama sent.
        base = {"tok_per_sec": 20.0, "ttft_s": 0.5, "total_s": 2.5,
                "ram": "1.9 GB in RAM"}
        self.assertIn("40 out / n/a in", format_stats_line(dict(
            base, eval_count=40, prompt_eval_count=None)))
        self.assertIn("n/a out / 128 in", format_stats_line(dict(
            base, eval_count=None, prompt_eval_count=128)))

    def test_compute_non_number_counts_are_none(self):
        # A non-number is not a count: the sanitizer drops it before
        # the formatter ever sees it.
        stats = compute_turn_stats({"eval_count": "many",
                                    "prompt_eval_count": "lots"})
        self.assertIsNone(stats["eval_count"])
        self.assertIsNone(stats["prompt_eval_count"])

    def test_format_session_line(self):
        self.assertEqual(format_session_line({"out": 0, "in": 0}),
                         "Session: 0 out / 0 in")
        self.assertEqual(format_session_line({"out": 42, "in": 128}),
                         "Session: 42 out / 128 in")

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


class RelayPresetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "duo-relay-presets.json")
        self.config = {"start_side": "B", "mode": "aware", "turn_cap": 3,
                       "stop_phrases": ["banana", "stop now"],
                       "stop_on_agreement": True,
                       "review_each_hop": False}

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(load_presets(self.path), {})

    def test_corrupt_file_reads_as_empty(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(load_presets(self.path), {})
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"presets": ["not", "a", "dict"]}, fh)
        self.assertEqual(load_presets(self.path), {})

    def test_corrupt_file_is_detected(self):
        self.assertFalse(presets_file_corrupt(self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertTrue(presets_file_corrupt(self.path))
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"presets": ["not", "a", "dict"]}, fh)
        self.assertTrue(presets_file_corrupt(self.path))
        # Valid JSON with an empty presets object is not corrupt.
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump({"presets": {}}, fh)
        self.assertFalse(presets_file_corrupt(self.path))

    def test_save_load_round_trip_exact_contents(self):
        save_preset(self.path, "debate", self.config)
        with open(self.path, "r", encoding="utf-8") as fh:
            on_disk = json.load(fh)
        self.assertEqual(on_disk, {"presets": {"debate": self.config}})
        self.assertEqual(load_presets(self.path),
                         {"debate": self.config})

    def test_save_keeps_other_presets_and_overwrites(self):
        save_preset(self.path, "one", self.config)
        other = dict(self.config, start_side="A", mode="blind",
                     turn_cap=10, stop_phrases=[],
                     stop_on_agreement=False, review_each_hop=True)
        save_preset(self.path, "two", other)
        newer = dict(self.config, start_side="A", mode="aware",
                     turn_cap=50, stop_phrases=["done"],
                     stop_on_agreement=False, review_each_hop=True)
        save_preset(self.path, "one", newer)
        presets = load_presets(self.path)
        self.assertEqual(set(presets), {"one", "two"})
        self.assertEqual(presets["one"], newer)
        self.assertEqual(presets["two"], other)

    def test_delete_preset_removes_only_that_preset(self):
        save_preset(self.path, "one", self.config)
        save_preset(self.path, "two", self.config)
        self.assertTrue(delete_preset(self.path, "one"))
        presets = load_presets(self.path)
        self.assertEqual(set(presets), {"two"})
        self.assertEqual(presets["two"], self.config)

    def test_delete_preset_missing_name_is_noop(self):
        save_preset(self.path, "one", self.config)
        self.assertFalse(delete_preset(self.path, "nope"))
        self.assertEqual(set(load_presets(self.path)), {"one"})
        self.assertFalse(delete_preset(self.path, "nope"))

    def test_presets_path_env_override(self):
        with mock.patch.dict(os.environ,
                             {"DUAL_CHAT_PRESETS_FILE":
                              "/tmp/elsewhere-presets.json"}):
            self.assertEqual(presets_path(),
                             "/tmp/elsewhere-presets.json")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(presets_path().endswith(
                "duo-relay-presets.json"))

    def test_invalid_presets_are_skipped_not_fatal(self):
        bad = [
            dict(self.config, start_side="C"),
            dict(self.config, mode="loud"),
            dict(self.config, turn_cap="three"),
            dict(self.config, turn_cap=True),
            dict(self.config, stop_phrases="banana"),
            dict(self.config, stop_phrases=["ok", 7]),
            dict(self.config, stop_on_agreement="yes"),
            dict(self.config, review_each_hop=1),
        ]
        del bad[0]["start_side"]  # missing field, not just wrong
        data = {"presets": {"good": self.config}}
        for i, config in enumerate(bad):
            data["presets"]["bad-%d" % i] = config
        data["presets"]["not-a-dict"] = "hello"
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.assertEqual(load_presets(self.path),
                         {"good": self.config})
        # The validator itself: every bad config fails, the good one
        # passes, and a non-dict fails.
        for config in bad:
            self.assertFalse(is_valid_preset(config))
        self.assertFalse(is_valid_preset("hello"))
        self.assertTrue(is_valid_preset(self.config))

    def test_out_of_range_cap_is_valid_and_clamped_on_apply(self):
        # A hand-edited cap outside 1-50 is still a preset -- the
        # spinbox clamps it when applied, like a relay clamps it.
        config = dict(self.config, turn_cap=99)
        self.assertTrue(is_valid_preset(config))
        save_preset(self.path, "big", config)
        self.assertEqual(load_presets(self.path)["big"]["turn_cap"], 99)

    def test_save_leaves_no_temp_behind(self):
        save_preset(self.path, "one", self.config)
        leftovers = [n for n in os.listdir(self.tmp.name)
                     if n.startswith(".duo-presets-")]
        self.assertEqual(leftovers, [])
        self.assertEqual(set(os.listdir(self.tmp.name)),
                         {"duo-relay-presets.json"})

    def test_save_failure_leaves_old_file_intact(self):
        save_preset(self.path, "one", self.config)
        with open(self.path, "rb") as fh:
            before = fh.read()
        with mock.patch("dual_chat.os.replace",
                        side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                save_preset(self.path, "two", self.config)
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(load_presets(self.path), {"one": self.config})
        leftovers = [n for n in os.listdir(self.tmp.name)
                     if n.startswith(".duo-presets-")]
        self.assertEqual(leftovers, [])

    def test_delete_failure_leaves_old_file_intact(self):
        save_preset(self.path, "one", self.config)
        save_preset(self.path, "two", self.config)
        with open(self.path, "rb") as fh:
            before = fh.read()
        with mock.patch("dual_chat.os.replace",
                        side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                delete_preset(self.path, "one")
        with open(self.path, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(set(load_presets(self.path)), {"one", "two"})
        leftovers = [n for n in os.listdir(self.tmp.name)
                     if n.startswith(".duo-presets-")]
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
            len(self.state["convo"].sides["A"].messages) >= 2
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
            [m.content for m in convo.sides["A"].messages],
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
            [m.content for m in convo.sides["A"].messages],
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
            [m.content for m in convo.sides["A"].messages],
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
            len(self.state["convo"].sides["A"].messages) >= 2
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
        self.assertEqual(len(self.state["convo"].sides["A"].messages), 6)

    def test_stats_show_dashes_before_any_turn(self):
        dashes = ("-- tok/s · -- out / -- in · first token -- · "
                  "turn -- · RAM --")
        self.assertEqual(self._stats("A"), dashes)
        self.assertEqual(self._stats("B"), dashes)

    def test_stats_after_turn_show_exact_canned_values(self):
        self.client.metrics = {"fake-a:latest": {
            "eval_count": 40, "eval_duration_ns": 2000000000,
            "prompt_eval_count": 128,
            "ttft_s": 0.5, "total_s": 2.5}}
        self.client.ps = {"fake-a:latest": 1900000000}
        self.assertTrue(self._send_to_a_and_wait("Hello"))
        self.assertEqual(
            self._stats("A"),
            "20.0 tok/s · 40 out / 128 in · first token 0.50s · "
            "turn 2.5s · 1.9 GB in RAM")
        # B hasn't run: still dashes.
        self.assertEqual(
            self._stats("B"),
            "-- tok/s · -- out / -- in · first token -- · "
            "turn -- · RAM --")

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
            "tok/s n/a · tokens n/a · first token n/a · turn n/a · "
            "not loaded")

    def test_stats_update_after_relay_turns(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self.client.metrics = {
            "fake-a:latest": {"eval_count": 10,
                              "eval_duration_ns": 1000000000,
                              "prompt_eval_count": 50,
                              "ttft_s": 0.1, "total_s": 1.0},
            "fake-b:latest": {"eval_count": 30,
                              "eval_duration_ns": 1000000000,
                              "prompt_eval_count": 75,
                              "ttft_s": 0.2, "total_s": 1.5}}
        self.client.ps = {"fake-a:latest": 1900000000,
                          "fake-b:latest": 1500000000}
        self._start_relay("Talk about fruit.", cap=2)
        self.assertTrue(self._wait_for_relay_stopped())
        self.assertEqual(
            self._stats("A"),
            "10.0 tok/s · 10 out / 50 in · first token 0.10s · "
            "turn 1.0s · 1.9 GB in RAM")
        self.assertEqual(
            self._stats("B"),
            "30.0 tok/s · 30 out / 75 in · first token 0.20s · "
            "turn 1.5s · 1.5 GB in RAM")

    def test_stats_reset_when_side_cleared(self):
        self.assertTrue(self._send_to_a_and_wait("Hello"))
        self.assertNotEqual(
            self._stats("A"),
            "-- tok/s · -- out / -- in · first token -- · "
            "turn -- · RAM --")
        self.state["widgets"]["A"]["clear"].click()
        self.assertEqual(
            self._stats("A"),
            "-- tok/s · -- out / -- in · first token -- · "
            "turn -- · RAM --")

    def test_summarize_replaces_older_turns(self):
        self.client.replies = {"fake-a:latest": [
            "alpha one", "alpha two", "alpha three",
            "They discussed fruit and picked apple pie."]}
        self.assertTrue(self._send_to_a_and_wait("first message"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        self.assertTrue(self._send_to_a_and_wait("third message"))
        convo = self.state["convo"]
        self.assertEqual(len(convo.sides["A"].messages), 6)
        summarize_btn = self.state["widgets"]["A"]["summarize"]
        self.assertTrue(summarize_btn.isEnabled())
        summarize_btn.click()
        ok = self._wait_for(lambda: (
            len(convo.sides["A"].messages) == 5
            and convo.sides["A"].messages[0].label == SUMMARY_LABEL
            and not self.state["state"]["busy"]), timeout=15)
        self.assertTrue(ok, "history should fold into summary + last 4")
        self.assertEqual(
            [m.content for m in convo.sides["A"].messages],
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
            len(side.messages) == 5
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
        self.assertEqual(side.messages[0].content,
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
        self.assertEqual(side.messages[0].content, "one")

    def test_display_survives_summarize_for_kept_entries(self):
        side = ChatSide("A")
        for i in range(6):
            side.add_user("msg %d" % i, display="D%d" % i)
        self.assertTrue(side.summarize("a summary"))
        self.assertEqual(len(side.messages), 5)
        # The summary entry itself has no override; the kept four do.
        self.assertEqual(side.display_text(0), side.messages[0].content)
        self.assertEqual([side.display_text(i) for i in range(1, 5)],
                         ["D2", "D3", "D4", "D5"])

    def test_clear_clears_display_too(self):
        side = ChatSide("A")
        side.add_user("x", display="X")
        side.clear()
        self.assertEqual([m.display for m in side.messages], [])

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
            len(self.state["convo"].sides["A"].messages) >= 2
            and len(self.state["convo"].sides["B"].messages) >= 2
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
            len(self.state["convo"].sides["A"].messages) >= 2
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
        self.assertEqual(self.state["convo"].sides["A"].messages[0].label,
                         "You [task]")
        self.assertEqual(self.state["convo"].sides["B"].messages[0].label,
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
# Multi-round tasks (v0.22, audit T11): GUI, fake Ollama
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class TaskRoundsGuiTests(unittest.TestCase):
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

    def _run_task(self, worker_index, follow_up_index, text, rounds=1):
        self.state["task_edit"].setPlainText(text)
        self.state["task_worker"].setCurrentIndex(worker_index)
        self.state["task_follow_up"].setCurrentIndex(follow_up_index)
        self.state["task_rounds"].setValue(rounds)
        self.app.processEvents()
        self.state["task_run"].click()

    def _last_messages_for(self, model):
        for seen_model, messages in reversed(self.client.messages_seen):
            if seen_model == model:
                return messages
        return None

    def test_rounds_control_defaults(self):
        rounds = self.state["task_rounds"]
        self.assertEqual(rounds.value(), 1)
        self.assertEqual(rounds.minimum(), TASK_ROUNDS_MIN)
        self.assertEqual(rounds.maximum(), TASK_ROUNDS_MAX)
        # Enabled with nothing picked: it only locks for "Nothing"
        # or mid-task.
        self.assertTrue(rounds.isEnabled())

    def test_rounds_clamps(self):
        rounds = self.state["task_rounds"]
        rounds.setValue(0)
        self.assertEqual(rounds.value(), TASK_ROUNDS_MIN)
        rounds.setValue(99)
        self.assertEqual(rounds.value(), TASK_ROUNDS_MAX)

    def test_rounds_disabled_with_nothing_follow_up(self):
        rounds = self.state["task_rounds"]
        self.state["task_follow_up"].setCurrentIndex(2)  # Nothing
        self.app.processEvents()
        self.assertFalse(rounds.isEnabled())
        self.state["task_follow_up"].setCurrentIndex(0)  # Review it
        self.app.processEvents()
        self.assertTrue(rounds.isEnabled())
        self.state["task_follow_up"].setCurrentIndex(1)  # Continue it
        self.app.processEvents()
        self.assertTrue(rounds.isEnabled())

    def test_one_round_review_is_v021_behavior(self):
        # Rounds 1 with Review it is the v0.21 task: the worker,
        # then one review round -- never the worker alone.
        self.client.replies = {
            "fake-a:latest": ["Draft one."],
            "fake-b:latest": ["Looks good."]}
        self._run_task(0, 0, "Write a draft.", rounds=1)
        self.assertTrue(self._wait_idle())
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest"])
        self.assertEqual(self._last_messages_for("fake-b:latest"),
                         [{"role": "user", "content":
                           TASK_REVIEW_TEMPLATE % "Draft one."}])
        b_text = self.state["widgets"]["B"]["transcript"].toPlainText()
        self.assertIn("You [task handoff]: "
                      "Task result from Model A, for review.", b_text)

    def test_three_round_continue(self):
        self.client.replies = {
            "fake-a:latest": ["Draft one.", "Draft three."],
            "fake-b:latest": ["Part two."]}
        self._run_task(0, 1, "Write the story.", rounds=3)
        self.assertTrue(self._wait_idle())
        # Three generations, sides alternating worker -> other ->
        # worker, one at a time throughout.
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest",
                          "fake-a:latest"])
        self.assertEqual(self.client.max_active, 1)
        # Round 2: B continues A's draft.
        self.assertEqual(self._last_messages_for("fake-b:latest"),
                         [{"role": "user", "content":
                           TASK_CONTINUE_TEMPLATE % "Draft one."}])
        # Round 3: A continues B's part, on top of its own history.
        a_messages = self._last_messages_for("fake-a:latest")
        self.assertEqual(len(a_messages), 3)
        self.assertEqual(a_messages[0],
                         {"role": "user",
                          "content": "Write the story."})
        self.assertEqual(a_messages[-1],
                         {"role": "user", "content":
                          TASK_CONTINUE_TEMPLATE % "Part two."})
        # The panes hold all three results with the right tags.
        a_text = self.state["widgets"]["A"]["transcript"].toPlainText()
        b_text = self.state["widgets"]["B"]["transcript"].toPlainText()
        self.assertIn("You [task]: Write the story.", a_text)
        self.assertIn("You [task handoff]: "
                      "Task result from Model B, to continue.", a_text)
        self.assertIn("You [task handoff]: "
                      "Task result from Model A, to continue.", b_text)
        self.assertIn("Draft one.", a_text)
        self.assertIn("Draft three.", a_text)
        self.assertIn("Part two.", b_text)
        self.assertIn("Task done.",
                      self.window.statusBar().currentMessage())

    def test_three_round_review_revises(self):
        self.client.replies = {
            "fake-a:latest": ["Draft one.", "Revised draft."],
            "fake-b:latest": ["Needs work."]}
        self._run_task(0, 0, "Write a draft.", rounds=3)
        self.assertTrue(self._wait_idle())
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest",
                          "fake-a:latest"])
        # Round 2: B reviews A's draft.
        self.assertEqual(self._last_messages_for("fake-b:latest"),
                         [{"role": "user", "content":
                           TASK_REVIEW_TEMPLATE % "Draft one."}])
        # Round 3: A revises from B's review.
        a_messages = self._last_messages_for("fake-a:latest")
        self.assertEqual(len(a_messages), 3)
        self.assertEqual(a_messages[-1],
                         {"role": "user", "content":
                          TASK_REVISE_TEMPLATE % "Needs work."})
        # The handoff notes name what is passing each way.
        a_text = self.state["widgets"]["A"]["transcript"].toPlainText()
        b_text = self.state["widgets"]["B"]["transcript"].toPlainText()
        self.assertIn("You [task handoff]: "
                      "Task result from Model A, for review.", b_text)
        self.assertIn("You [task handoff]: "
                      "Review from Model B, to revise.", a_text)
        self.assertIn("Revised draft.", a_text)

    def test_four_round_review_alternates(self):
        self.client.replies = {
            "fake-a:latest": ["Draft one.", "Revised draft."],
            "fake-b:latest": ["Needs work.", "Better now."]}
        self._run_task(0, 0, "Write a draft.", rounds=4)
        self.assertTrue(self._wait_idle())
        self.assertEqual(self.client.calls,
                         ["fake-a:latest", "fake-b:latest",
                          "fake-a:latest", "fake-b:latest"])
        # Round 4 is a review turn again: B reviews A's revision.
        b_messages = self._last_messages_for("fake-b:latest")
        self.assertEqual(len(b_messages), 3)
        self.assertEqual(b_messages[-1],
                         {"role": "user", "content":
                          TASK_REVIEW_TEMPLATE % "Revised draft."})

    def test_nothing_follow_up_is_one_round(self):
        # "Nothing" locks the Rounds box and forces one round even
        # when the box somehow holds a bigger number.
        self.state["task_follow_up"].setCurrentIndex(2)
        self.app.processEvents()
        self.assertFalse(self.state["task_rounds"].isEnabled())
        self.state["task_rounds"].setValue(5)
        self.state["task_edit"].setPlainText("Write a draft.")
        self.state["task_worker"].setCurrentIndex(0)
        self.app.processEvents()
        self.state["task_run"].click()
        self.assertTrue(self._wait_idle())
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        self.assertIn("Task done.",
                      self.window.statusBar().currentMessage())

    def test_rounds_setup_locked_mid_task(self):
        self.client.delay = 0.5
        self.client.replies = {"fake-a:latest":
                               ["one two three four five"]}
        self._run_task(0, 1, "Write the story.", rounds=3)
        self.assertTrue(self._wait_for(
            lambda: self.state["state"]["busy"], timeout=5))
        self.assertFalse(self.state["task_rounds"].isEnabled())
        self.assertFalse(self.state["task_edit"].isEnabled())
        self.assertTrue(self._wait_idle())
        self.assertTrue(self.state["task_rounds"].isEnabled())

    def test_stop_mid_chain_ends_chain(self):
        self.client.delay = 0.5
        self.client.replies = {"fake-a:latest":
                               ["one two three four five"]}
        self._run_task(0, 1, "Write the story.", rounds=3)
        self.assertTrue(self._wait_for(
            lambda: self.state["state"]["busy"], timeout=5))
        self.state["widgets"]["A"]["stop"].click()
        self.assertTrue(self._wait_for(
            lambda: "Task stopped." in
            self.window.statusBar().currentMessage(), timeout=10))
        self.assertTrue(self._wait_idle())
        self.assertIsNone(self.state["state"]["task"])
        # No further rounds: the chain ended with the stop.
        self.assertEqual(self.client.calls, ["fake-a:latest"])

    def test_failure_mid_chain_ends_chain(self):
        from dual_chat import build_window

        class FailOnSecondClient(FakeGuiClient):
            def chat_stream(self, model, messages, on_chunk,
                            cancel_event, timeout=1800, on_metrics=None,
                            on_retry=None, options=None):
                if model == "fake-b:latest":
                    # Round 2 fails after partial text.
                    with self.lock:
                        self.calls.append(model)
                        self.messages_seen.append(
                            (model, copy.deepcopy(messages)))
                    on_chunk("partial ")
                    raise OllamaError(MSG_OLLAMA_SLOW)
                return super().chat_stream(
                    model, messages, on_chunk, cancel_event, timeout,
                    on_metrics, on_retry, options)

        client = FailOnSecondClient()
        client.replies = {"fake-a:latest": ["Draft one."]}
        window = build_window(client, logger=TEST_LOGGER)
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state["widgets"]["A"]["picker"].count() == 2))
            state["widgets"]["A"]["picker"].setCurrentIndex(0)
            state["widgets"]["B"]["picker"].setCurrentIndex(1)
            self.app.processEvents()
            state["task_edit"].setPlainText("Write the story.")
            state["task_worker"].setCurrentIndex(0)
            state["task_follow_up"].setCurrentIndex(1)  # Continue it
            state["task_rounds"].setValue(3)
            self.app.processEvents()
            state["task_run"].click()
            self.assertTrue(self._wait_for(
                lambda: "Task failed:" in
                window.statusBar().currentMessage(), timeout=15))
            self.assertTrue(self._wait_for(
                lambda: state["state"]["task"] is None, timeout=10))
            # Two calls and no more: round 3 never ran.
            self.assertEqual(client.calls,
                             ["fake-a:latest", "fake-b:latest"])
            # The round-1 result stays in A's pane; B keeps the
            # partial text plus the failure note.
            a_text = state["widgets"]["A"]["transcript"].toPlainText()
            b_text = state["widgets"]["B"]["transcript"].toPlainText()
            self.assertIn("Draft one.", a_text)
            self.assertIn("partial", b_text)
        finally:
            window.close()

    def test_status_shows_round_progress(self):
        self.client.delay = 0.3
        self.client.replies = {
            "fake-a:latest": ["one two three four five",
                              "six seven eight nine ten"],
            "fake-b:latest": ["alpha beta gamma delta epsilon"]}
        self._run_task(0, 1, "Write the story.", rounds=3)
        self.assertTrue(self._wait_for(
            lambda: "Task: round 1 of 3..." in
            self.window.statusBar().currentMessage(), timeout=10))
        self.assertTrue(self._wait_for(
            lambda: "Task: round 2 of 3..." in
            self.window.statusBar().currentMessage(), timeout=15))
        self.assertTrue(self._wait_idle())
        self.assertIn("Task done.",
                      self.window.statusBar().currentMessage())


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


class FullLogCapTests(unittest.TestCase):
    """The full-log cap (v0.27, audit T16): the combined
    chronological log is bounded, oldest first, and the export says
    so when entries were trimmed."""
    WHEN = datetime.datetime(2026, 11, 13, 13, 30)

    def _entry(self, n):
        return ("A", "You", "message %d" % n)

    def test_append_under_cap_never_trims(self):
        log = []
        for n in range(FULL_LOG_MAX_ENTRIES):
            self.assertFalse(append_full_log_entry(log, self._entry(n)))
        self.assertEqual(len(log), FULL_LOG_MAX_ENTRIES)
        self.assertEqual(log[0], self._entry(0))
        self.assertEqual(log[-1], self._entry(FULL_LOG_MAX_ENTRIES - 1))

    def test_append_past_cap_trims_oldest_first(self):
        log = [self._entry(n) for n in range(FULL_LOG_MAX_ENTRIES)]
        trimmed = append_full_log_entry(
            log, self._entry(FULL_LOG_MAX_ENTRIES))
        self.assertTrue(trimmed)
        self.assertEqual(len(log), FULL_LOG_MAX_ENTRIES)
        # The oldest entry is gone; the newest N survive, in order.
        self.assertEqual(
            log, [self._entry(n)
                  for n in range(1, FULL_LOG_MAX_ENTRIES + 1)])

    def test_one_append_trims_a_log_already_over_cap(self):
        # A log loaded from a file saved by an older version can
        # start over the cap; the next append pulls it back down.
        log = [self._entry(n)
               for n in range(FULL_LOG_MAX_ENTRIES + 50)]
        self.assertTrue(append_full_log_entry(log, self._entry(99999)))
        self.assertEqual(len(log), FULL_LOG_MAX_ENTRIES)
        self.assertEqual(log[0], self._entry(51))
        self.assertEqual(log[-1], self._entry(99999))

    def test_export_carries_note_only_when_trimmed(self):
        log = [("A", "You", "hi"), ("B", "fake-b:latest", "hello")]
        text = format_full_transcript(log, self.WHEN, trimmed=True)
        blocks = text.split("\n\n")
        self.assertEqual(
            blocks[0], "DUO transcript -- saved 2026-11-13 13:30")
        self.assertEqual(blocks[1], FULL_LOG_TRIMMED_NOTE)
        self.assertEqual(blocks[2], "A -- You: hi")
        self.assertEqual(blocks[3], "B -- fake-b:latest: hello\n")
        plain = format_full_transcript(log, self.WHEN)
        self.assertNotIn(FULL_LOG_TRIMMED_NOTE, plain)

    def test_export_note_wording(self):
        self.assertEqual(
            FULL_LOG_TRIMMED_NOTE,
            "(Earlier messages were trimmed to keep the log bounded.)")

    def test_export_empty_log_has_no_note_even_when_trimmed(self):
        self.assertEqual(
            format_full_transcript([], self.WHEN, trimmed=True),
            "DUO transcript -- saved 2026-11-13 13:30\n\n"
            "No messages yet.\n")

    def test_formatting_never_changes_the_log(self):
        log = [("A", "You", "hi")]
        before = list(log)
        format_full_transcript(log, self.WHEN, trimmed=True)
        self.assertEqual(log, before)


class TranscriptImportTests(unittest.TestCase):
    """Transcript import (v0.23, audit T12): parsing a saved
    single-pane transcript back into history entries."""

    def _side_with(self, model="fake-a:latest"):
        side = ChatSide("A")
        side.model = model
        return side

    def test_round_trip_restores_history_labels_display(self):
        side = self._side_with()
        side.add_user("hello there")
        side.add_assistant("hi back")
        side.add_user("line one\nline two")
        entries = parse_side_transcript(format_side_transcript(side))
        self.assertEqual(
            [(entry["role"], entry["content"], entry["label"])
             for entry in entries],
            [("user", "hello there", "You"),
             ("assistant", "hi back", "fake-a:latest"),
             ("user", "line one\nline two", "You")])

    def test_header_and_empty_marker_skipped(self):
        # An empty export is a header plus "No messages yet." -- both
        # are skipped, and the file parses to zero messages.
        side = self._side_with()
        text = format_side_transcript(side)
        self.assertIn("No messages yet.", text)
        with self.assertRaisesRegex(TranscriptError,
                                    "no messages in it"):
            parse_side_transcript(text)

    def test_multiline_bodies_preserved(self):
        side = self._side_with()
        side.add_user("para one\n\npara two")
        side.add_assistant("first line\nsecond line")
        entries = parse_side_transcript(format_side_transcript(side))
        self.assertEqual(entries[0]["content"], "para one\n\npara two")
        self.assertEqual(entries[1]["content"],
                         "first line\nsecond line")

    def test_trailing_space_in_body_preserved(self):
        # Streamed replies end with a trailing space per word, so a
        # round-trip must keep a trailing space, not strip it.
        side = self._side_with()
        side.add_assistant("Rainy day. ")
        entries = parse_side_transcript(format_side_transcript(side))
        self.assertEqual(entries[0]["content"], "Rainy day. ")

    def test_relay_and_task_labels_preserved(self):
        side = self._side_with()
        side.add_user("hop text", label="You [relay from Model B]")
        side.add_user("task text", label="You [task]")
        side.add_user("framed handoff", label="You [task handoff]",
                      display="Task result from Model B, for review.")
        entries = parse_side_transcript(format_side_transcript(side))
        self.assertEqual(
            [(entry["role"], entry["label"]) for entry in entries],
            [("user", "You [relay from Model B]"),
             ("user", "You [task]"),
             ("user", "You [task handoff]")])
        # The export carries the display text, not the full framed
        # handoff -- so the import restores what the window showed.
        self.assertEqual(
            entries[2]["content"],
            "Task result from Model B, for review.")

    def test_summary_entry_parses_as_user(self):
        side = self._side_with()
        side.add_user("one")
        side.add_assistant("two")
        side.add_user("three")
        side.add_assistant("four")
        side.add_user("five")
        self.assertTrue(side.summarize("short version"))
        entries = parse_side_transcript(format_side_transcript(side))
        self.assertEqual(entries[0]["label"], SUMMARY_LABEL)
        self.assertEqual(entries[0]["role"], "user")
        self.assertIn("short version", entries[0]["content"])

    def test_garbage_file_rejected(self):
        with self.assertRaisesRegex(
                TranscriptError, "doesn't look like a DUO transcript"):
            parse_side_transcript("hello world\n")

    def test_zero_message_file_rejected(self):
        with self.assertRaisesRegex(TranscriptError,
                                    "no messages in it"):
            parse_side_transcript(
                "DUO transcript -- Model A (a) -- saved 2026-11-13 "
                "00:00\n\nNo messages yet.\n")

    def test_blocks_without_labels_rejected(self):
        with self.assertRaisesRegex(
                TranscriptError, "no messages DUO can read"):
            parse_side_transcript(
                "DUO transcript -- Model A (a) -- saved 2026-11-13 "
                "00:00\n\nno label here\n")

    def test_save_both_file_rejected(self):
        convo = Conversation()
        convo.sides["A"].model = "fake-a:latest"
        convo.sides["B"].model = "fake-b:latest"
        convo.sides["A"].add_user("hi")
        convo.sides["B"].add_user("hey")
        with self.assertRaisesRegex(
                TranscriptError,
                "holds both sides.*single-side transcript"):
            parse_side_transcript(format_both_transcript(convo))

    def test_full_transcript_file_rejected(self):
        log = [("A", "You", "hi"), ("B", "fake-b:latest", "yo")]
        with self.assertRaisesRegex(
                TranscriptError,
                "is a full transcript.*single-side transcript"):
            parse_side_transcript(format_full_transcript(log))

    def test_load_side_transcript_file(self):
        side = self._side_with()
        side.add_user("hello")
        side.add_assistant("hi back")
        with tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False,
                encoding="utf-8") as fh:
            fh.write(format_side_transcript(side))
            path = fh.name
        try:
            entries = load_side_transcript_file(path)
        finally:
            os.unlink(path)
        self.assertEqual(
            [(entry["role"], entry["content"]) for entry in entries],
            [("user", "hello"), ("assistant", "hi back")])

    def test_load_missing_file_raises_oserror(self):
        with self.assertRaises(OSError):
            load_side_transcript_file(
                os.path.join(tempfile.gettempdir(),
                             "duo-no-such-transcript.txt"))


class MarkdownTests(unittest.TestCase):
    """Markdown rendering (v0.24, audit T13): the bounded converter.

    Escape-first, then the subset: bold, italic, inline code,
    fenced code blocks, headers 1-3, lists, blockquotes, http/https
    links. Incomplete constructs stay literal; anything outside the
    subset stays literal too."""

    def test_bold(self):
        self.assertEqual(
            markdown_to_html("Hello **bold** end."),
            "<p>Hello <b>bold</b> end.</p>")

    def test_italic_star(self):
        self.assertEqual(
            markdown_to_html("Hello *ital* end."),
            "<p>Hello <i>ital</i> end.</p>")

    def test_italic_underscore(self):
        self.assertEqual(
            markdown_to_html("Hello _ital_ end."),
            "<p>Hello <i>ital</i> end.</p>")

    def test_inline_code(self):
        self.assertEqual(
            markdown_to_html("Run `print(x)` now."),
            "<p>Run <code>print(x)</code> now.</p>")

    def test_code_span_protects_markdown(self):
        # Nothing inside a code span is formatted.
        self.assertEqual(
            markdown_to_html("Literal `**not bold**` here."),
            "<p>Literal <code>**not bold**</code> here.</p>")

    def test_fenced_block(self):
        self.assertEqual(
            markdown_to_html("```\nx = 1\ny = 2\n```"),
            '<pre style="background-color:#f0f0f0;">x = 1\ny = 2</pre>')

    def test_fenced_block_escapes_and_ignores_markdown(self):
        self.assertEqual(
            markdown_to_html("```\n<b>tag</b> and **stars**\n```"),
            '<pre style="background-color:#f0f0f0;">'
            "&lt;b&gt;tag&lt;/b&gt; and **stars**</pre>")

    def test_unclosed_fence_runs_to_end(self):
        self.assertEqual(
            markdown_to_html("intro\n```\ncode to eof"),
            "<p>intro</p>"
            '<pre style="background-color:#f0f0f0;">code to eof</pre>')

    def test_headers(self):
        self.assertEqual(markdown_to_html("# Title"),
                         "<h1>Title</h1>")
        self.assertEqual(markdown_to_html("## Sub"),
                         "<h2>Sub</h2>")
        self.assertEqual(markdown_to_html("### Small"),
                         "<h3>Small</h3>")

    def test_header_level_four_stays_literal(self):
        self.assertEqual(markdown_to_html("#### Not a header"),
                         "<p>#### Not a header</p>")

    def test_header_with_inline_markdown(self):
        self.assertEqual(markdown_to_html("## A **bold** head"),
                         "<h2>A <b>bold</b> head</h2>")

    def test_unordered_list(self):
        self.assertEqual(
            markdown_to_html("- one\n- two\n* three"),
            "<ul><li>one</li><li>two</li><li>three</li></ul>")

    def test_ordered_list(self):
        self.assertEqual(
            markdown_to_html("1. first\n2. second"),
            "<ol><li>first</li><li>second</li></ol>")

    def test_list_items_take_inline_markdown(self):
        self.assertEqual(
            markdown_to_html("- **bold** item\n- `code` item"),
            "<ul><li><b>bold</b> item</li>"
            "<li><code>code</code> item</li></ul>")

    def test_blockquote(self):
        self.assertEqual(
            markdown_to_html("> quoted words\n> more quote"),
            "<blockquote>quoted words more quote</blockquote>")

    def test_link(self):
        self.assertEqual(
            markdown_to_html("see [example](https://example.com) here."),
            '<p>see <a href="https://example.com">example</a> here.</p>')

    def test_link_http_allowed(self):
        self.assertIn(
            '<a href="http://example.com">',
            markdown_to_html("[x](http://example.com)"))

    def test_link_non_http_stays_literal(self):
        # No javascript: (or any other scheme) ever becomes a link.
        self.assertEqual(
            markdown_to_html("see [x](javascript:alert(1)) here."),
            "<p>see [x](javascript:alert(1)) here.</p>")

    def test_escaping_raw_html_shown_as_text(self):
        self.assertEqual(
            markdown_to_html("raw <b>not bold</b> text"),
            "<p>raw &lt;b&gt;not bold&lt;/b&gt; text</p>")

    def test_escaping_script_tag(self):
        self.assertNotIn("<script>",
                         markdown_to_html("<script>alert(1)</script>"))
        self.assertIn("&lt;script&gt;",
                      markdown_to_html("<script>alert(1)</script>"))

    def test_unclosed_bold_stays_literal(self):
        self.assertEqual(
            markdown_to_html("unclosed **bold stays literal"),
            "<p>unclosed **bold stays literal</p>")

    def test_unclosed_italic_stays_literal(self):
        self.assertEqual(
            markdown_to_html("unclosed *ital stays literal"),
            "<p>unclosed *ital stays literal</p>")

    def test_snake_case_not_italic(self):
        self.assertEqual(
            markdown_to_html("snake_case stays plain"),
            "<p>snake_case stays plain</p>")

    def test_asterisk_math_not_italic(self):
        self.assertEqual(
            markdown_to_html("2 * 3 * 4 stays plain"),
            "<p>2 * 3 * 4 stays plain</p>")

    def test_mixed_content(self):
        self.assertEqual(
            markdown_to_html(
                "# Title\n\nHello **bold** and `code`.\n\n- one\n- two"),
            "<h1>Title</h1>"
            "<p>Hello <b>bold</b> and <code>code</code>.</p>"
            "<ul><li>one</li><li>two</li></ul>")

    def test_empty_string(self):
        self.assertEqual(markdown_to_html(""), "")
        self.assertEqual(markdown_to_html("   \n  "), "")

    def test_multiline_paragraph_keeps_line_breaks(self):
        self.assertEqual(
            markdown_to_html("line one\nline two"),
            "<p>line one<br>line two</p>")

    def test_crlf_normalized(self):
        self.assertEqual(
            markdown_to_html("one\r\ntwo"),
            "<p>one<br>two</p>")


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
            len(self.state["convo"].sides["A"].messages) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _send_both_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_both"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].messages) >= 2
            and len(self.state["convo"].sides["B"].messages) >= 2
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
        self.assertIn("hello", self.state["convo"].sides["A"].messages[0].content)

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
            len(self.state["convo"].sides["A"].messages) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _send_both_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_both"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].messages) >= 2
            and len(self.state["convo"].sides["B"].messages) >= 2
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
        # (No trailing space on the reply: since v0.24 the finished
        # reply is re-rendered from the history text, and the fake
        # client streams "word " chunks -- the live document used to
        # carry a trailing space the history never had. The
        # re-render path never had it either, so the two paths now
        # agree.)
        self.assertEqual(self._fragments("A"), [
            ("You: ", True),
            ("hello", False),
            ("fake-a:latest: ", True),
            ("Reply from fake-a:latest.", False),
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
            [m.content for m in self.state["convo"].sides["A"].messages],
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
            len(convo.sides["A"].messages) == 5
            and convo.sides["A"].messages[0].label == SUMMARY_LABEL
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


class FullLogCapGuiTests(unittest.TestCase):
    """The full-log cap in the window (v0.27, audit T16): trimming
    through the real append point, the export note, the flag rules
    for load and clear, and the cap covering sends and imports."""
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
            len(self.state["convo"].sides["A"].messages) >= 2
            and len(self.state["convo"].sides["B"].messages) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _save_dialog(self, path):
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getSaveFileName",
            return_value=(path, "Text files (*.txt)"))

    def _open_dialog(self, path):
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getOpenFileName",
            return_value=(path, "Text files (*.txt)"))

    def _read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _trim_via_log_event(self):
        # Drive the real append point past the real cap: no Qt work
        # per call, just list appends, so the full loop is fast.
        # Entries alternate sides, so a one-side clear still leaves
        # the other side's entries behind.
        log_event = self.state["log_event"]
        total = FULL_LOG_MAX_ENTRIES + 5
        for n in range(total):
            log_event("A" if n % 2 == 0 else "B",
                      "You", "message %d" % n)
        return total

    def test_log_event_trims_at_the_real_cap_and_sets_the_flag(self):
        total = self._trim_via_log_event()
        state = self.state["state"]
        self.assertEqual(len(state["full_log"]), FULL_LOG_MAX_ENTRIES)
        self.assertTrue(state["full_log_trimmed"])
        # Oldest first: the first five entries are gone, the rest
        # survive in order.
        self.assertEqual(state["full_log"][0],
                         ("B", "You", "message 5"))
        self.assertEqual(state["full_log"][-1],
                         ("A", "You", "message %d" % (total - 1)))
        # Trimming never touches the per-pane histories.
        self.assertEqual(self.state["convo"].sides["A"].messages, [])
        self.assertEqual(self.state["convo"].sides["B"].messages, [])

    def test_export_carries_the_note_after_a_trim(self):
        self._trim_via_log_event()
        path = os.path.join(self.tmp.name, "full.txt")
        with self._save_dialog(path):
            self.state["save_full"].click()
        text = self._read(path)
        blocks = text.split("\n\n")
        self.assertEqual(blocks[1], FULL_LOG_TRIMMED_NOTE)
        self.assertNotIn("message 0", text)
        self.assertIn("message 5", text)

    def test_export_has_no_note_without_a_trim(self):
        self.assertTrue(self._send_both_and_wait("hello both"))
        self.assertFalse(self.state["state"]["full_log_trimmed"])
        path = os.path.join(self.tmp.name, "full.txt")
        with self._save_dialog(path):
            self.state["save_full"].click()
        self.assertNotIn(FULL_LOG_TRIMMED_NOTE, self._read(path))

    def test_sends_trim_when_the_cap_is_small(self):
        cap = 3
        with mock.patch("duo_transcript.FULL_LOG_MAX_ENTRIES", cap):
            self.assertTrue(self._send_both_and_wait("hello both"))
            state = self.state["state"]
            self.assertTrue(state["full_log_trimmed"])
            self.assertEqual(len(state["full_log"]), cap)
            # Both "You" lines land before either reply, so the
            # oldest entry -- A's "You" -- is the one that drops.
            self.assertEqual(
                state["full_log"],
                [("B", "You", "hello both"),
                 ("A", "fake-a:latest", "Reply from fake-a:latest."),
                 ("B", "fake-b:latest", "Reply from fake-b:latest.")])
            # Histories are whole: trimming only ever cuts the log.
            self.assertEqual(
                [m.content for m in
                 self.state["convo"].sides["A"].messages],
                ["hello both", "Reply from fake-a:latest."])
            self.assertEqual(
                [m.content for m in
                 self.state["convo"].sides["B"].messages],
                ["hello both", "Reply from fake-b:latest."])

    def test_import_goes_through_the_cap(self):
        cap = 3
        count = 6
        text = ("DUO transcript -- Model A (fake-a:latest) -- "
                "saved 2026-11-13 13:30\n\n"
                + "\n\n".join("You: imported %d" % n
                              for n in range(count)) + "\n")
        path = os.path.join(self.tmp.name, "side-a.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        with mock.patch("duo_transcript.FULL_LOG_MAX_ENTRIES", cap):
            with self._open_dialog(path):
                self.state["widgets"]["A"]["import"].click()
            self.app.processEvents()
            state = self.state["state"]
            self.assertTrue(state["full_log_trimmed"])
            self.assertEqual(len(state["full_log"]), cap)
            self.assertEqual(
                state["full_log"],
                [("A", "You", "imported %d" % n)
                 for n in range(count - cap, count)])
            # All six messages landed in the pane; only the log
            # was capped.
            self.assertEqual(
                len(self.state["convo"].sides["A"].messages), count)

    def test_load_resets_the_trimmed_flag(self):
        self._trim_via_log_event()
        self.assertTrue(self.state["state"]["full_log_trimmed"])
        # A small conversation file, written through the real saver.
        convo = self.state["convo"]
        path = os.path.join(self.tmp.name, "convo.json")
        save_conversation_file(path, convo, [("A", "You", "kept")])
        with self._open_dialog(path):
            self.state["open_convo"].click()
        self.app.processEvents()
        self.assertEqual(self.state["state"]["full_log"],
                         [("A", "You", "kept")])
        self.assertFalse(self.state["state"]["full_log_trimmed"])

    def test_clear_keeps_the_flag_until_the_log_is_empty(self):
        self._trim_via_log_event()
        state = self.state["state"]
        self.assertTrue(state["full_log_trimmed"])
        # The trimmed log holds both sides; clearing one leaves the
        # other side's entries and the flag.
        self.state["widgets"]["A"]["clear"].click()
        self.app.processEvents()
        self.assertTrue(state["full_log"])
        self.assertTrue(state["full_log_trimmed"])
        self.state["widgets"]["B"]["clear"].click()
        self.app.processEvents()
        self.assertEqual(state["full_log"], [])
        self.assertFalse(state["full_log_trimmed"])


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
        # The constant lives in duo_ollama since the v0.25 split --
        # patch it there, where the client reads it.
        self._patcher = mock.patch("duo_ollama.CHAT_RETRY_DELAYS",
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
        with mock.patch("duo_ollama.CHAT_RETRY_DELAYS", (30.0, 30.0)):
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
            len(self.state["convo"].sides["A"].messages) >= 2
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
            and len(self.state["convo"].sides["A"].messages) == 2),
            timeout=15))
        self.assertEqual(self.client.calls, ["fake-a:latest"])
        self.assertEqual(self.state["convo"].sides["B"].messages, [])
        self.assertEqual(
            [m.content for m in self.state["convo"].sides["A"].messages],
            ["Just for A.", "Reply from fake-a:latest."])

    def test_phone_send_to_both(self):
        self._pick_models()
        res = self._post_send({"text": "Both, please.", "target": "Both"})
        self.assertTrue(res["ok"])
        self.assertTrue(self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].messages) == 2
            and len(self.state["convo"].sides["B"].messages) == 2),
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
                len(self.state["convo"].sides["A"].messages) == 2),
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
            len(self.state["convo"].sides["A"].messages) == 2
            and len(self.state["convo"].sides["B"].messages) == 2),
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
            and len(self.state["convo"].sides["A"].messages) == 2
            and len(self.state["convo"].sides["B"].messages) == 2),
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
        for version in ["0.%d" % n for n in range(1, 32)]:
            self.assertIn(version, log)

    def test_gitignore_covers_runtime_files(self):
        ignored = self._read(".gitignore")
        for entry in ("__pycache__/", "*.pyc", ".venv/", "duo-debug.log",
                      "duo-pairs.json", "duo-relay-presets.json",
                      "duo.png", "*.zip"):
            self.assertIn(entry, ignored)

    def test_gitignore_covers_secret_files(self):
        # Secrets are never in code: they live in files the repo
        # ignores, so a key or token can never be committed by
        # accident. Every category the rule names must be covered.
        ignored = self._read(".gitignore")
        for entry in (".env", ".env.*", "*.env", "providers.json",
                      "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*",
                      "credentials*.json", "*secret*.json",
                      "local-config.json", "config.local.*", ".netrc"):
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

    def test_readme_notes_mit_license_and_clone_modes(self):
        readme = self._read("README.md")
        self.assertIn("License: MIT", readme)
        self.assertNotIn("not decided yet", readme)
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
            self.assertEqual(restored.sides[side].messages,
                             convo.sides[side].messages)
            self.assertEqual([m.label for m in restored.sides[side].messages],
                             [m.label for m in convo.sides[side].messages])
            self.assertEqual([m.display for m in restored.sides[side].messages],
                             [m.display for m in convo.sides[side].messages])
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
                restored.sides["A"].messages[-1].content,
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
            len(self.state["convo"].sides["A"].messages) >= 2
            and len(self.state["convo"].sides["B"].messages) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _send_to_a_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_a"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].messages) >= 2
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
            side: (copy.deepcopy(convo.sides[side].messages),
                   [m.label for m in convo.sides[side].messages],
                   [m.display for m in convo.sides[side].messages],
                   convo.sides[side].instruction,
                   convo.sides[side].model)
            for side in ("A", "B")}

    def _assert_snapshot(self, snap):
        convo = self.state["convo"]
        for side in ("A", "B"):
            history, labels, display, instruction, model = snap[side]
            self.assertEqual(convo.sides[side].messages, history)
            self.assertEqual([m.label for m in convo.sides[side].messages], labels)
            self.assertEqual([m.display for m in convo.sides[side].messages], display)
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
        self.assertEqual(self.state["convo"].sides["A"].messages, [])
        self.assertEqual(self.state["convo"].sides["B"].messages, [])
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
        self.assertEqual(self.state["convo"].sides["A"].messages,
                         [ChatMessage("user", "hello", "You")])
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
            self.state["convo"].sides["A"].messages,
            [ChatMessage("user", "fresh load", "You")])

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
            len(self.state["convo"].sides["A"].messages) == 2
            and len(self.state["convo"].sides["B"].messages) == 2),
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


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class TokenCountGuiTests(unittest.TestCase):
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

    def _stats(self, side):
        return self.state["widgets"][side]["stats"].text()

    def _session(self, side):
        return self.state["widgets"][side]["session_tokens"].text()

    def _send_to_a_and_wait(self, text):
        before = len(self.state["convo"].sides["A"].messages)
        self.state["composer"].setPlainText(text)
        self.state["send_a"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].messages) > before
            and not self.state["state"]["busy"]), timeout=15)

    def _send_to_both_and_wait(self, text):
        before_a = len(self.state["convo"].sides["A"].messages)
        before_b = len(self.state["convo"].sides["B"].messages)
        self.state["composer"].setPlainText(text)
        self.state["send_both"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].messages) > before_a
            and len(self.state["convo"].sides["B"].messages) > before_b
            and not self.state["state"]["busy"]), timeout=15)

    def test_session_labels_start_at_zero(self):
        for side in ("A", "B"):
            label = self.state["widgets"][side]["session_tokens"]
            self.assertTrue(label.isVisible())
            self.assertEqual(label.text(), "Session: 0 out / 0 in")

    def test_turn_counts_show_in_stats_line_and_session_label(self):
        self.client.metrics = {"fake-a:latest": {
            "eval_count": 42, "eval_duration_ns": 2000000000,
            "prompt_eval_count": 128,
            "ttft_s": 0.5, "total_s": 2.5}}
        self.assertTrue(self._send_to_a_and_wait("Hello"))
        self.assertIn("42 out / 128 in", self._stats("A"))
        self.assertEqual(self._session("A"), "Session: 42 out / 128 in")
        # B hasn't run: its total is still zero.
        self.assertEqual(self._session("B"), "Session: 0 out / 0 in")

    def test_session_totals_accumulate_across_turns(self):
        self.client.metrics = {"fake-a:latest": {
            "eval_count": 42, "eval_duration_ns": 2000000000,
            "prompt_eval_count": 128,
            "ttft_s": 0.5, "total_s": 2.5}}
        self.assertTrue(self._send_to_a_and_wait("first"))
        self.assertEqual(self._session("A"), "Session: 42 out / 128 in")
        # Different counts on the second turn: the total is the sum.
        self.client.metrics = {"fake-a:latest": {
            "eval_count": 8, "eval_duration_ns": 1000000000,
            "prompt_eval_count": 22,
            "ttft_s": 0.1, "total_s": 1.0}}
        self.assertTrue(self._send_to_a_and_wait("second"))
        self.assertEqual(self._session("A"), "Session: 50 out / 150 in")
        # The stats line shows only the latest turn's counts.
        self.assertIn("8 out / 22 in", self._stats("A"))

    def test_send_and_relay_hop_accumulate(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self.client.metrics = {
            "fake-a:latest": {"eval_count": 10,
                              "eval_duration_ns": 1000000000,
                              "prompt_eval_count": 50,
                              "ttft_s": 0.1, "total_s": 1.0},
            "fake-b:latest": {"eval_count": 30,
                              "eval_duration_ns": 1000000000,
                              "prompt_eval_count": 75,
                              "ttft_s": 0.2, "total_s": 1.5}}
        self.assertTrue(self._send_to_both_and_wait("Hello, both."))
        self.assertEqual(self._session("A"), "Session: 10 out / 50 in")
        self.assertEqual(self._session("B"), "Session: 30 out / 75 in")
        # A 2-turn relay: A generates once (the seed), B once (the hop).
        self.state["start_picker"].setCurrentIndex(0)  # A starts
        self.state["cap_spin"].setValue(2)
        self.state["mode_picker"].setCurrentIndex(0)  # Blind
        self.state["composer"].setPlainText("Talk to each other.")
        self.app.processEvents()
        self.state["relay_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["relay_status"].text().startswith(
                "Relay stopped"), timeout=20))
        self.assertEqual(self._session("A"), "Session: 20 out / 100 in")
        self.assertEqual(self._session("B"), "Session: 60 out / 150 in")

    def test_clear_leaves_session_totals_untouched(self):
        self.client.metrics = {"fake-a:latest": {
            "eval_count": 42, "eval_duration_ns": 2000000000,
            "prompt_eval_count": 128,
            "ttft_s": 0.5, "total_s": 2.5}}
        self.assertTrue(self._send_to_a_and_wait("Hello"))
        self.assertEqual(self._session("A"), "Session: 42 out / 128 in")
        self.state["widgets"]["A"]["clear"].click()
        self.app.processEvents()
        # The stats line resets to dashes, the session total stays.
        self.assertEqual(
            self._stats("A"),
            "-- tok/s · -- out / -- in · first token -- · "
            "turn -- · RAM --")
        self.assertEqual(self._session("A"), "Session: 42 out / 128 in")

    def test_failed_turn_shows_na_and_adds_nothing(self):
        from dual_chat import build_window

        class FailingClient(FakeGuiClient):
            def chat_stream(self, model, messages, on_chunk,
                            cancel_event, timeout=1800, on_metrics=None,
                            on_retry=None, options=None):
                # Partial text streams, then the turn fails -- the
                # v0.10 shape: the partial reply stays, the counts
                # are withheld.
                on_chunk("partial ")
                raise OllamaError(MSG_OLLAMA_SLOW)

        window = build_window(FailingClient(), logger=TEST_LOGGER)
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
                lambda: "partial" in pane.toPlainText(), timeout=10))
            self.assertTrue(self._wait_for(
                lambda: state["widgets"]["A"]["stats"].text().startswith(
                    "tok/s n/a"), timeout=10))
            self.assertEqual(
                state["widgets"]["A"]["stats"].text(),
                "tok/s n/a · tokens n/a · first token n/a · "
                "turn n/a · RAM n/a")
            self.assertEqual(
                state["widgets"]["A"]["session_tokens"].text(),
                "Session: 0 out / 0 in")
        finally:
            window.close()

    def test_summarize_counts_toward_session_total_not_stats_line(self):
        self.client.replies = {"fake-a:latest": [
            "alpha one", "alpha two", "alpha three",
            "They discussed fruit and picked apple pie."]}
        self.client.metrics = {"fake-a:latest": {
            "eval_count": 10, "eval_duration_ns": 1000000000,
            "prompt_eval_count": 50,
            "ttft_s": 0.1, "total_s": 1.0}}
        self.assertTrue(self._send_to_a_and_wait("first message"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        self.assertTrue(self._send_to_a_and_wait("third message"))
        self.assertEqual(self._session("A"), "Session: 30 out / 150 in")
        # Give the summarize turn unmistakable counts, so a stats
        # line overwritten by it would be visible.
        self.client.metrics = {"fake-a:latest": {
            "eval_count": 99, "eval_duration_ns": 1000000000,
            "prompt_eval_count": 999,
            "ttft_s": 0.1, "total_s": 1.0}}
        convo = self.state["convo"]
        summarize_btn = self.state["widgets"]["A"]["summarize"]
        self.assertTrue(summarize_btn.isEnabled())
        summarize_btn.click()
        ok = self._wait_for(lambda: (
            len(convo.sides["A"].messages) == 5
            and convo.sides["A"].messages[0].label == SUMMARY_LABEL
            and not self.state["state"]["busy"]), timeout=15)
        self.assertTrue(ok, "history should fold into summary + last 4")
        # The summarize turn's tokens count toward the session total...
        self.assertEqual(self._session("A"), "Session: 129 out / 1149 in")
        # ...but the stats line still shows the last real turn.
        self.assertIn("10 out / 50 in", self._stats("A"))
        self.assertNotIn("99 out / 999 in", self._stats("A"))


# ---------------------------------------------------------------------------
# Relay preset GUI tests (offscreen)
# ---------------------------------------------------------------------------

@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests need DUAL_CHAT_GUI_TESTS=1")
class RelayPresetGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        self.client.reset()
        # Presets (and pairs) go to temp files so tests never touch
        # the real ones next to the app.
        self.tmp = tempfile.TemporaryDirectory()
        self.presets_file = os.path.join(self.tmp.name,
                                         "duo-relay-presets.json")
        self.pairs_file = os.path.join(self.tmp.name, "duo-pairs.json")
        self._env = mock.patch.dict(os.environ, {
            "DUAL_CHAT_PRESETS_FILE": self.presets_file,
            "DUAL_CHAT_PAIRS_FILE": self.pairs_file})
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

    def _relay_row(self):
        """The relay row as a plain dict, for exact comparisons."""
        s = self.state
        return {
            "start_side": s["start_picker"].currentText(),
            "mode": ("aware" if s["mode_picker"].currentText() == "Aware"
                     else "blind"),
            "turn_cap": s["cap_spin"].value(),
            "stop_phrases": parse_stop_phrases(s["stop_edit"].text()),
            "stop_on_agreement": s["agree_check"].isChecked(),
            "review_each_hop": s["review_check"].isChecked(),
        }

    def _set_relay_row(self, config):
        s = self.state
        s["start_picker"].setCurrentText(config["start_side"])
        s["mode_picker"].setCurrentText(
            "Aware" if config["mode"] == "aware" else "Blind")
        s["cap_spin"].setValue(config["turn_cap"])
        s["stop_edit"].setText(", ".join(config["stop_phrases"]))
        s["agree_check"].setChecked(config["stop_on_agreement"])
        s["review_check"].setChecked(config["review_each_hop"])
        self.app.processEvents()

    def _seed_presets(self, *names):
        config = {"start_side": "B", "mode": "aware", "turn_cap": 3,
                  "stop_phrases": ["banana"],
                  "stop_on_agreement": True, "review_each_hop": False}
        for name in names:
            save_preset(self.presets_file, name, config)
        self.state["refresh_preset_combo"]()
        self.app.processEvents()
        return config

    def _status(self):
        return self.window.statusBar().currentMessage()

    def test_nothing_loaded_by_default(self):
        s = self.state
        self.assertEqual(s["preset_combo"].currentIndex(), -1)
        self.assertEqual(s["preset_combo"].count(), 0)
        self.assertFalse(s["preset_delete"].isEnabled())
        # The relay row sits at its defaults.
        self.assertEqual(self._relay_row(), {
            "start_side": "A", "mode": "blind", "turn_cap": 10,
            "stop_phrases": [], "stop_on_agreement": False,
            "review_each_hop": False})

    def test_save_and_load_round_trip_sets_every_control(self):
        config = {"start_side": "B", "mode": "aware", "turn_cap": 3,
                  "stop_phrases": ["banana", "stop now"],
                  "stop_on_agreement": True, "review_each_hop": False}
        self._set_relay_row(config)
        self.state["preset_name"].setText("debate")
        self.state["preset_save"].click()
        self.app.processEvents()
        self.assertEqual(load_presets(self.presets_file),
                         {"debate": config})
        self.assertIn("Relay preset 'debate' saved.", self._status())
        # Scramble the row, deselect, then pick the preset: the row
        # comes back exactly.
        self.state["preset_combo"].setCurrentIndex(-1)
        self._set_relay_row({
            "start_side": "A", "mode": "blind", "turn_cap": 50,
            "stop_phrases": [], "stop_on_agreement": False,
            "review_each_hop": True})
        self.state["preset_combo"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertEqual(self._relay_row(), config)
        self.assertIn("Relay preset 'debate' loaded.", self._status())
        # Re-picking the same preset still loads it (activated, not
        # just currentIndexChanged): scramble, re-pick, row returns.
        self._set_relay_row({
            "start_side": "A", "mode": "blind", "turn_cap": 50,
            "stop_phrases": [], "stop_on_agreement": False,
            "review_each_hop": True})
        self.state["preset_combo"].activated.emit(0)
        self.app.processEvents()
        self.assertEqual(self._relay_row(), config)

    def test_save_overwrites_with_newer_values(self):
        first = {"start_side": "A", "mode": "blind", "turn_cap": 10,
                 "stop_phrases": [], "stop_on_agreement": False,
                 "review_each_hop": False}
        second = {"start_side": "B", "mode": "aware", "turn_cap": 3,
                  "stop_phrases": ["banana"],
                  "stop_on_agreement": True, "review_each_hop": True}
        self._set_relay_row(first)
        self.state["preset_name"].setText("same name")
        self.state["preset_save"].click()
        self.app.processEvents()
        self._set_relay_row(second)
        self.state["preset_save"].click()
        self.app.processEvents()
        self.assertEqual(load_presets(self.presets_file),
                         {"same name": second})
        self.assertEqual(self.state["preset_combo"].count(), 1)

    def test_save_with_empty_name_asks_for_a_name(self):
        self.state["preset_name"].setText("   ")
        self.state["preset_save"].click()
        self.app.processEvents()
        self.assertIn("Name the preset first.", self._status())
        self.assertFalse(os.path.exists(self.presets_file))
        self.assertEqual(self.state["preset_combo"].count(), 0)

    def test_delete_accept_removes_preset_from_file_and_picker(self):
        from PySide6.QtWidgets import QMessageBox
        self._seed_presets("one", "two")
        self.state["preset_combo"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertEqual(self.state["preset_combo"].currentText(), "one")
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.state["preset_delete"].click()
        self.app.processEvents()
        self.assertEqual(set(load_presets(self.presets_file)), {"two"})
        self.assertEqual(self.state["preset_combo"].count(), 1)
        self.assertEqual(self.state["preset_combo"].itemText(0), "two")
        # Nothing is selected after a delete -- never auto-pick.
        self.assertEqual(self.state["preset_combo"].currentIndex(), -1)
        self.assertFalse(self.state["preset_delete"].isEnabled())
        self.assertIn("Relay preset 'one' deleted.", self._status())
        # Delete the last one: the picker is empty, still nothing
        # selected, Delete stays off.
        self.state["preset_combo"].setCurrentIndex(0)
        self.app.processEvents()
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.Yes):
            self.state["preset_delete"].click()
        self.app.processEvents()
        self.assertEqual(load_presets(self.presets_file), {})
        self.assertEqual(self.state["preset_combo"].count(), 0)
        self.assertEqual(self.state["preset_combo"].currentIndex(), -1)
        self.assertFalse(self.state["preset_delete"].isEnabled())

    def test_delete_reject_keeps_preset(self):
        from PySide6.QtWidgets import QMessageBox
        self._seed_presets("one", "two")
        self.state["preset_combo"].setCurrentIndex(0)
        self.app.processEvents()
        with mock.patch.object(
                QMessageBox, "question",
                return_value=QMessageBox.StandardButton.No):
            self.state["preset_delete"].click()
        self.app.processEvents()
        self.assertEqual(set(load_presets(self.presets_file)),
                         {"one", "two"})
        self.assertEqual(self.state["preset_combo"].count(), 2)
        self.assertEqual(self.state["preset_combo"].currentIndex(), 0)

    def test_corrupt_file_shows_a_plain_note_and_changes_nothing(self):
        config = {"start_side": "B", "mode": "aware", "turn_cap": 3,
                  "stop_phrases": ["banana"],
                  "stop_on_agreement": True, "review_each_hop": False}
        self._set_relay_row(config)
        with open(self.presets_file, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.state["refresh_preset_combo"]()
        self.app.processEvents()
        self.assertIn("Couldn't read the saved relay presets.",
                      self._status())
        self.assertEqual(self.state["preset_combo"].count(), 0)
        # The row is exactly as it was; the app still works.
        self.assertEqual(self._relay_row(), config)
        self.assertTrue(self.state["preset_save"].isEnabled())

    def test_invalid_preset_in_file_is_skipped(self):
        good = {"start_side": "B", "mode": "aware", "turn_cap": 3,
                "stop_phrases": ["banana"],
                "stop_on_agreement": True, "review_each_hop": False}
        bad = dict(good, mode="loud")
        with open(self.presets_file, "w", encoding="utf-8") as fh:
            json.dump({"presets": {"good": good, "bad": bad}}, fh)
        self.state["refresh_preset_combo"]()
        self.app.processEvents()
        self.assertEqual(self.state["preset_combo"].count(), 1)
        self.assertEqual(self.state["preset_combo"].itemText(0), "good")
        # The file itself is valid JSON, so no corrupt-file note.
        self.assertNotIn("Couldn't read the saved relay presets.",
                         self._status())

    def test_preset_controls_wait_for_idle_during_a_relay(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self._seed_presets("one")
        s = self.state
        # Idle: everything is on (Delete, with a preset selected).
        s["preset_combo"].setCurrentIndex(0)
        self.app.processEvents()
        self.assertTrue(s["preset_combo"].isEnabled())
        self.assertTrue(s["preset_save"].isEnabled())
        self.assertTrue(s["preset_delete"].isEnabled())
        # Start a relay and hold it at a review pause: the preset
        # row goes off, like the pair row does. (The row is set
        # explicitly here -- the seeded preset's stop phrase would
        # match the scripted "banana split" reply and end the relay
        # before it ever pauses.)
        s["start_picker"].setCurrentIndex(0)  # A starts
        s["cap_spin"].setValue(10)
        s["mode_picker"].setCurrentIndex(0)  # Blind
        s["stop_edit"].setText("")
        s["agree_check"].setChecked(False)
        s["review_check"].setChecked(True)
        s["composer"].setPlainText("Talk about fruit.")
        s["relay_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: s["review_frame"].isVisible(), timeout=15))
        self.assertFalse(s["preset_combo"].isEnabled())
        self.assertFalse(s["preset_name"].isEnabled())
        self.assertFalse(s["preset_save"].isEnabled())
        self.assertFalse(s["preset_delete"].isEnabled())
        # End the relay: the row comes back.
        s["review_stop"].click()
        self.assertTrue(self._wait_for(
            lambda: s["relay_status"].text().startswith("Relay stopped"),
            timeout=15))
        self.assertTrue(s["preset_combo"].isEnabled())
        self.assertTrue(s["preset_save"].isEnabled())
        self.assertTrue(s["preset_delete"].isEnabled())

    def test_failed_save_leaves_old_file_and_says_so(self):
        self._seed_presets("one")
        with open(self.presets_file, "rb") as fh:
            before = fh.read()
        self.state["preset_name"].setText("two")
        with mock.patch("dual_chat.os.replace",
                        side_effect=OSError("disk full")):
            self.state["preset_save"].click()
        self.app.processEvents()
        with open(self.presets_file, "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertEqual(set(load_presets(self.presets_file)), {"one"})
        self.assertIn("Couldn't save relay preset 'two': disk full",
                      self._status())


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class TranscriptSearchGuiTests(unittest.TestCase):
    """Transcript search (v0.23, audit T12): the per-pane search row
    -- match counting, highlighting, prev/next cycling, and the
    search clearing whenever the transcript changes."""

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

    def _seed_a(self):
        side = self.state["convo"].sides["A"]
        side.add_user("the rain in Spain")
        side.add_assistant("rain, rain, go away")
        side.add_user("no weather here")
        self.state["render_transcript"]("A")
        self.app.processEvents()

    def _widgets_a(self):
        return self.state["widgets"]["A"]

    def test_search_row_present(self):
        w = self._widgets_a()
        self.assertEqual(w["search_edit"].placeholderText(),
                         "Search this conversation…")
        self.assertEqual(w["search_prev"].text(), "Previous")
        self.assertEqual(w["search_next"].text(), "Next")
        self.assertEqual(w["search_count"].text(), "")

    def test_match_count_and_case_insensitive(self):
        self._seed_a()
        w = self._widgets_a()
        w["search_edit"].setText("RAIN")
        self.app.processEvents()
        # "rain" appears once in the first message, twice in the
        # second -- and the uppercase query still finds them all.
        self.assertEqual(w["search_count"].text(), "3 matches")
        selections = w["transcript"].extraSelections()
        self.assertEqual(len(selections), 3)
        # The current match (the first) is orange, the rest yellow.
        self.assertEqual(
            selections[0].format.background().color().name(),
            "#ffa500")
        self.assertEqual(
            selections[1].format.background().color().name(),
            "#ffff00")
        self.assertEqual(
            selections[2].format.background().color().name(),
            "#ffff00")

    def test_prev_next_cycle_with_wrap(self):
        self._seed_a()
        w = self._widgets_a()
        w["search_edit"].setText("rain")
        self.app.processEvents()
        self.assertEqual(w["search_count"].text(), "3 matches")
        w["search_next"].click()
        self.assertEqual(w["search_count"].text(), "2 of 3")
        w["search_next"].click()
        self.assertEqual(w["search_count"].text(), "3 of 3")
        # Past the last match wraps back to the first.
        w["search_next"].click()
        self.assertEqual(w["search_count"].text(), "1 of 3")
        w["search_prev"].click()
        self.assertEqual(w["search_count"].text(), "3 of 3")

    def test_empty_search_clears(self):
        self._seed_a()
        w = self._widgets_a()
        w["search_edit"].setText("rain")
        self.app.processEvents()
        self.assertEqual(len(w["transcript"].extraSelections()), 3)
        w["search_edit"].setText("")
        self.app.processEvents()
        self.assertEqual(w["search_count"].text(), "")
        self.assertEqual(w["transcript"].extraSelections(), [])

    def test_no_match_label(self):
        self._seed_a()
        w = self._widgets_a()
        w["search_edit"].setText("zebra")
        self.app.processEvents()
        self.assertEqual(w["search_count"].text(), "No matches")
        self.assertEqual(w["transcript"].extraSelections(), [])
        # Stepping with no matches does nothing.
        w["search_next"].click()
        self.assertEqual(w["search_count"].text(), "No matches")

    def test_search_cleared_on_pane_clear(self):
        self._seed_a()
        w = self._widgets_a()
        w["search_edit"].setText("rain")
        self.app.processEvents()
        self.assertEqual(w["search_count"].text(), "3 matches")
        w["clear"].click()
        self.app.processEvents()
        self.assertEqual(w["search_edit"].text(), "")
        self.assertEqual(w["search_count"].text(), "")
        self.assertEqual(w["transcript"].extraSelections(), [])

    def test_search_cleared_on_rerender(self):
        self._seed_a()
        w = self._widgets_a()
        w["search_edit"].setText("rain")
        self.app.processEvents()
        self.assertEqual(w["search_count"].text(), "3 matches")
        self.state["render_transcript"]("A")
        self.app.processEvents()
        self.assertEqual(w["search_edit"].text(), "")
        self.assertEqual(w["search_count"].text(), "")
        self.assertEqual(w["transcript"].extraSelections(), [])

    def test_search_cleared_on_new_message(self):
        self._seed_a()
        w = self._widgets_a()
        w["search_edit"].setText("rain")
        self.app.processEvents()
        self.assertEqual(w["search_count"].text(), "3 matches")
        self.state["composer"].setPlainText("more weather talk")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self.assertEqual(w["search_edit"].text(), "")
        self.assertEqual(w["search_count"].text(), "")
        self.assertEqual(w["transcript"].extraSelections(), [])

    def test_search_is_display_only(self):
        self._seed_a()
        side = self.state["convo"].sides["A"]
        history_before = copy.deepcopy(side.messages)
        labels_before = [m.label for m in side.messages]
        display_before = [m.display for m in side.messages]
        export_before = format_side_transcript(side)
        w = self._widgets_a()
        w["search_edit"].setText("rain")
        self.app.processEvents()
        w["search_next"].click()
        w["search_prev"].click()
        w["search_edit"].setText("")
        self.app.processEvents()
        self.assertEqual(side.messages, history_before)
        self.assertEqual([m.label for m in side.messages], labels_before)
        self.assertEqual([m.display for m in side.messages], display_before)
        self.assertEqual(format_side_transcript(side), export_before)


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class TranscriptImportGuiTests(unittest.TestCase):
    """Transcript import (v0.23, audit T12): the per-pane Import
    button -- replacing a pane from a saved single-pane transcript,
    the confirmation, the rejections, and the full-log rule."""

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
            len(self.state["convo"].sides["A"].messages) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _send_both_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_both"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].messages) >= 2
            and len(self.state["convo"].sides["B"].messages) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _open_dialog(self, path):
        """Patch the open dialog to return ``path`` ("" cancels)."""
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getOpenFileName",
            return_value=(path, "Text files (*.txt)"))

    def _answer(self, button):
        from PySide6.QtWidgets import QMessageBox
        return mock.patch.object(QMessageBox, "question",
                                 return_value=button)

    def _write_file(self, name, text):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def _export_file(self, name="saved-a.txt"):
        """A single-pane export of a made-up conversation, with a
        model name the window never picked -- importing must not
        touch the picker."""
        side = ChatSide("A")
        side.model = "other-model:9b"
        side.add_user("what is the capital of France?")
        side.add_assistant("Paris.")
        side.add_user("and of Spain?\nasking for a friend")
        side.add_assistant("Madrid.")
        return self._write_file(name, format_side_transcript(side))

    def _status(self):
        return self.window.statusBar().currentMessage()

    def test_import_button_present_and_enabled_when_idle(self):
        w = self.state["widgets"]["A"]
        self.assertEqual(w["import"].text(), "Import")
        self.assertTrue(w["import"].isEnabled())

    def test_import_into_empty_pane_needs_no_confirm(self):
        from PySide6.QtWidgets import QMessageBox
        path = self._export_file()
        no_confirm = mock.patch.object(
            QMessageBox, "question",
            side_effect=AssertionError("confirm asked on empty pane"))
        with self._open_dialog(path), no_confirm:
            self.state["widgets"]["A"]["import"].click()
        self.app.processEvents()
        side = self.state["convo"].sides["A"]
        self.assertEqual(
            [(entry.role, entry.content) for entry in side.messages],
            [("user", "what is the capital of France?"),
             ("assistant", "Paris."),
             ("user", "and of Spain?\nasking for a friend"),
             ("assistant", "Madrid.")])
        self.assertEqual(
            [m.label for m in side.messages],
            ["You", "other-model:9b", "You", "other-model:9b"])
        self.assertEqual([m.display for m in side.messages], [None, None, None, None])
        self.assertEqual(self._status(),
                         "Transcript imported from %s" % path)

    def test_import_over_live_pane_yes_replaces(self):
        from PySide6.QtWidgets import QMessageBox
        self.assertTrue(self._send_to_a_and_wait("live message"))
        path = self._export_file()
        with self._open_dialog(path), self._answer(
                QMessageBox.StandardButton.Yes):
            self.state["widgets"]["A"]["import"].click()
        self.app.processEvents()
        side = self.state["convo"].sides["A"]
        self.assertEqual(len(side.messages), 4)
        self.assertEqual(side.messages[0].content,
                         "what is the capital of France?")

    def test_import_over_live_pane_no_keeps(self):
        from PySide6.QtWidgets import QMessageBox
        self.assertTrue(self._send_to_a_and_wait("live message"))
        before = copy.deepcopy(self.state["convo"].sides["A"].messages)
        path = self._export_file()
        with self._open_dialog(path), self._answer(
                QMessageBox.StandardButton.No):
            self.state["widgets"]["A"]["import"].click()
        self.app.processEvents()
        self.assertEqual(self.state["convo"].sides["A"].messages, before)

    def test_import_dialog_cancel_changes_nothing(self):
        self.assertTrue(self._send_to_a_and_wait("live message"))
        before = copy.deepcopy(self.state["convo"].sides["A"].messages)
        with self._open_dialog(""):
            self.state["widgets"]["A"]["import"].click()
        self.app.processEvents()
        self.assertEqual(self.state["convo"].sides["A"].messages, before)

    def test_rejected_file_changes_nothing(self):
        self.assertTrue(self._send_to_a_and_wait("live message"))
        before = copy.deepcopy(self.state["convo"].sides["A"].messages)
        convo = Conversation()
        convo.sides["A"].model = "fake-a:latest"
        convo.sides["B"].model = "fake-b:latest"
        convo.sides["A"].add_user("hi")
        convo.sides["B"].add_user("hey")
        path = self._write_file("both.txt",
                                format_both_transcript(convo))
        with self._open_dialog(path):
            self.state["widgets"]["A"]["import"].click()
        self.app.processEvents()
        self.assertEqual(self.state["convo"].sides["A"].messages, before)
        self.assertIn("holds both sides", self._status())
        garbage = self._write_file("garbage.txt", "hello world\n")
        with self._open_dialog(garbage):
            self.state["widgets"]["A"]["import"].click()
        self.app.processEvents()
        self.assertEqual(self.state["convo"].sides["A"].messages, before)
        self.assertIn("doesn't look like a DUO transcript",
                      self._status())

    def test_full_log_updated_per_rule(self):
        from PySide6.QtWidgets import QMessageBox
        self.assertTrue(self._send_both_and_wait("hello both"))
        log_before = list(self.state["state"]["full_log"])
        b_before = [entry for entry in log_before if entry[0] == "B"]
        self.assertTrue(b_before)
        path = self._export_file()
        with self._open_dialog(path), self._answer(
                QMessageBox.StandardButton.Yes):
            self.state["widgets"]["A"]["import"].click()
        self.app.processEvents()
        log = self.state["state"]["full_log"]
        # The other side's entries are untouched, in their order.
        self.assertEqual([entry for entry in log if entry[0] == "B"],
                         b_before)
        # The pane's old entries are gone; the imported ones sit at
        # the end, in file order.
        a_entries = [entry for entry in log if entry[0] == "A"]
        self.assertEqual(
            a_entries,
            [("A", "You", "what is the capital of France?"),
             ("A", "other-model:9b", "Paris."),
             ("A", "You", "and of Spain?\nasking for a friend"),
             ("A", "other-model:9b", "Madrid.")])
        self.assertEqual(log[-len(a_entries):], a_entries)

    def test_model_picker_untouched_after_import(self):
        path = self._export_file()
        with self._open_dialog(path):
            self.state["widgets"]["A"]["import"].click()
        self.app.processEvents()
        picker = self.state["widgets"]["A"]["picker"]
        self.assertEqual(picker.currentText(), "fake-a:latest")
        self.assertEqual(self.state["convo"].sides["A"].model,
                         "fake-a:latest")

    def test_send_after_import_includes_imported_history(self):
        path = self._export_file()
        with self._open_dialog(path):
            self.state["widgets"]["A"]["import"].click()
        self.app.processEvents()
        self.assertTrue(self._send_to_a_and_wait("what about Italy?"))
        _model, sent = self.client.messages_seen[-1]
        self.assertEqual(
            [(message["role"], message["content"]) for message in sent],
            [("user", "what is the capital of France?"),
             ("assistant", "Paris."),
             ("user", "and of Spain?\nasking for a friend"),
             ("assistant", "Madrid."),
             ("user", "what about Italy?")])


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class MarkdownGuiTests(unittest.TestCase):
    """Markdown rendering (v0.24, audit T13): assistant replies render
    as formatted text in the panes -- live streaming stays plain
    until the turn finishes, user messages stay literal, and nothing
    stored (history, exports) changes."""

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
            len(self.state["convo"].sides["A"].messages) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _transcript(self, side):
        return self.state["widgets"][side]["transcript"].toPlainText()

    def _fragments(self, side):
        """[(text, bold, italic, mono, href)] for every fragment in a
        pane's document, in order -- the rendered format, not just
        the plain string."""
        from PySide6 import QtGui
        doc = self.state["widgets"][side]["transcript"].document()
        out = []
        block = doc.firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    fmt = frag.charFormat()
                    # font().family(), not the deprecated
                    # fontFamily(): the deprecated call segfaults
                    # this PySide6 build (proven by faulthandler).
                    out.append((frag.text(),
                                fmt.fontWeight()
                                >= QtGui.QFont.Weight.Bold,
                                fmt.fontItalic(),
                                fmt.font().family() == "monospace",
                                fmt.anchorHref()))
                it += 1
            block = block.next()
        return out

    def _save_dialog(self, path):
        """Patch the save dialog to return ``path`` ("" cancels)."""
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getSaveFileName",
            return_value=(path, "Text files (*.txt)"))

    def _open_dialog(self, path):
        """Patch the open dialog to return ``path`` ("" cancels)."""
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getOpenFileName",
            return_value=(path, "Text files (*.txt)"))

    def _read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def test_completed_reply_renders_markdown(self):
        reply = ("## Findings\n\nThe **bold** word and `code()` here."
                 "\n\n- one\n- two\n\n"
                 "See [example](https://example.com).")
        self.client.replies = {"fake-a:latest": [reply]}
        self.assertTrue(self._send_to_a_and_wait("tell me"))
        plain = self._transcript("A")
        # The markers are gone from what the window shows...
        self.assertNotIn("**", plain)
        self.assertNotIn("`", plain)
        self.assertNotIn("##", plain)
        self.assertNotIn("[example]", plain)
        # ...but the words are all there, in order.
        self.assertIn("fake-a:latest: Findings", plain)
        self.assertIn("The bold word and code() here.", plain)
        self.assertIn("one\ntwo", plain)
        self.assertIn("See example.", plain)
        # ...and the formatting is real, not just stripped markers.
        frags = self._fragments("A")
        self.assertTrue(any(text == "bold" and bold
                            for text, bold, _i, _m, _h in frags),
                        frags)
        self.assertTrue(any(text == "code()" and mono
                            for text, _b, _i, mono, _h in frags),
                        frags)
        self.assertTrue(any(text == "example"
                            and href == "https://example.com"
                            for text, _b, _i, _m, href in frags),
                        frags)
        html = self.state["widgets"]["A"]["transcript"].toHtml()
        self.assertIn("font-size:x-large", html)  # the h2 header
        self.assertIn("<ul", html)
        self.assertIn("<li", html)
        # The history still holds the raw Markdown, untouched.
        self.assertEqual(
            self.state["convo"].sides["A"].messages[1].content, reply)

    def test_link_click_opens_browser(self):
        # Clicking a link in a pane opens it in the default browser:
        # render a reply with a link, click the link text in the
        # viewport with the opener mocked, and check the URL
        # reaches it.
        from PySide6 import QtCore, QtGui, QtTest
        reply = "See [example](https://example.com) here."
        self.client.replies = {"fake-a:latest": [reply]}
        self.assertTrue(self._send_to_a_and_wait("tell me"))
        transcript = self.state["widgets"]["A"]["transcript"]
        found = transcript.document().find("example")
        self.assertFalse(found.isNull())
        rect = transcript.cursorRect(found)
        with mock.patch.object(
                QtGui.QDesktopServices, "openUrl") as opener:
            QtTest.QTest.mouseClick(
                transcript.viewport(), QtCore.Qt.LeftButton,
                pos=rect.center())
            opener.assert_called_once()
            self.assertEqual(opener.call_args[0][0].toString(),
                             "https://example.com")

    def test_streaming_shows_plain_then_formats(self):
        # A long, slow reply: plenty of time to catch it mid-stream.
        reply = ("Here is **bold** text and `code` and more words "
                 "to keep it streaming for a while now.")
        self.client.replies = {"fake-a:latest": [reply]}
        self.client.delay = 0.3
        self.state["composer"].setPlainText("tell me")
        self.state["send_a"].click()
        # Mid-stream the raw markers are visible and unformatted.
        self.assertTrue(self._wait_for(
            lambda: "**bold**" in self._transcript("A"), timeout=10),
            "the raw markers should stream into the pane")
        mid = self._fragments("A")
        self.assertFalse(any("bold" in text and bold
                             for text, bold, _i, _m, _h in mid),
                         mid)
        # Once the turn finishes, the same text is formatted.
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self.assertNotIn("**", self._transcript("A"))
        done = self._fragments("A")
        self.assertTrue(any(text == "bold" and bold
                            for text, bold, _i, _m, _h in done),
                        done)

    def test_user_message_stays_literal(self):
        self.assertTrue(self._send_to_a_and_wait("say **not bold**"))
        plain = self._transcript("A")
        self.assertIn("You: say **not bold**", plain)
        frags = self._fragments("A")
        self.assertFalse(any("not bold" in text and bold
                             for text, bold, _i, _m, _h in frags),
                         frags)

    def test_export_keeps_raw_markdown(self):
        reply = "## Findings\n\nThe **bold** word."
        self.client.replies = {"fake-a:latest": [reply]}
        self.assertTrue(self._send_to_a_and_wait("tell me"))
        path = os.path.join(self.tmp.name, "out.txt")
        with self._save_dialog(path):
            self.state["widgets"]["A"]["save"].click()
        text = self._read(path)
        self.assertIn("fake-a:latest: ## Findings", text)
        self.assertIn("The **bold** word.", text)

    def test_search_finds_text_in_markdown_reply(self):
        reply = "The **bold** word and `code()` here."
        self.client.replies = {"fake-a:latest": [reply]}
        self.assertTrue(self._send_to_a_and_wait("tell me"))
        w = self.state["widgets"]["A"]
        w["search_edit"].setText("bold")
        self.app.processEvents()
        self.assertEqual(w["search_count"].text(), "1 matches")
        self.assertTrue(
            self.state["widgets"]["A"]["transcript"].extraSelections())

    def test_raw_html_shown_as_text(self):
        reply = "raw <b>not bold</b> and <script>alert(1)</script>"
        self.client.replies = {"fake-a:latest": [reply]}
        self.assertTrue(self._send_to_a_and_wait("tell me"))
        plain = self._transcript("A")
        self.assertIn("raw <b>not bold</b>", plain)
        self.assertIn("<script>alert(1)</script>", plain)
        frags = self._fragments("A")
        self.assertFalse(any("not bold" in text and bold
                             for text, bold, _i, _m, _h in frags),
                         frags)

    def test_summary_entry_renders_markdown(self):
        self.client.replies = {"fake-a:latest": [
            "alpha one", "alpha two", "alpha three",
            "They picked **apple pie** over cake."]}
        self.assertTrue(self._send_to_a_and_wait("first message"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        self.assertTrue(self._send_to_a_and_wait("third message"))
        convo = self.state["convo"]
        self.assertEqual(len(convo.sides["A"].messages), 6)
        self.state["widgets"]["A"]["summarize"].click()
        ok = self._wait_for(lambda: (
            len(convo.sides["A"].messages) == 5
            and convo.sides["A"].messages[0].label == SUMMARY_LABEL
            and not self.state["state"]["busy"]), timeout=15)
        self.assertTrue(ok, "history should fold into summary + last 4")
        # The summary entry is model output: it renders.
        self.assertNotIn("**", self._transcript("A"))
        frags = self._fragments("A")
        self.assertTrue(any(text == "apple pie" and bold
                            for text, bold, _i, _m, _h in frags),
                        frags)

    def test_failed_turn_stays_plain(self):
        from dual_chat import OllamaError

        class FailingClient(FakeGuiClient):
            def chat_stream(self, model, messages, on_chunk,
                            cancel_event, timeout=1800, on_metrics=None,
                            on_retry=None, options=None):
                on_chunk("**partial** ")
                raise OllamaError("boom")

        self.window.close()
        from dual_chat import build_window
        failing = FailingClient()
        self.window = build_window(failing, logger=TEST_LOGGER)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self._pick_models()
        self.state["composer"].setPlainText("tell me")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        # The partial text stayed plain: a failed turn never finished.
        self.assertIn("**partial**", self._transcript("A"))
        frags = self._fragments("A")
        self.assertFalse(any("partial" in text and bold
                             for text, bold, _i, _m, _h in frags),
                         frags)

    def test_imported_markdown_renders(self):
        side = ChatSide("A")
        side.model = "other-model:9b"
        side.add_user("what did you find?")
        side.add_assistant("The **bold** word.")
        path = os.path.join(self.tmp.name, "saved.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(format_side_transcript(side))
        with self._open_dialog(path):
            self.state["widgets"]["A"]["import"].click()
        self.app.processEvents()
        # The re-render path formats the imported assistant message.
        self.assertNotIn("**", self._transcript("A"))
        self.assertIn("The bold word.", self._transcript("A"))
        frags = self._fragments("A")
        self.assertTrue(any(text == "bold" and bold
                            for text, bold, _i, _m, _h in frags),
                        frags)

    def test_message_after_list_is_not_a_list_item(self):
        # A list reply must not swallow the next message into the
        # list (the close after insertHtml steps out cleanly).
        self.client.replies = {"fake-a:latest": ["- one\n- two"]}
        self.assertTrue(self._send_to_a_and_wait("list them"))
        self.assertTrue(self._send_to_a_and_wait("second message"))
        plain = self._transcript("A")
        self.assertIn("one\ntwo\n\nYou: second message", plain)
        doc = self.state["widgets"]["A"]["transcript"].document()
        block = doc.firstBlock()
        found = False
        while block.isValid():
            if "second message" in block.text():
                found = True
                self.assertIsNone(block.textList(),
                                  "the second message must not be "
                                  "inside the list")
            block = block.next()
        self.assertTrue(found, "second message block not found")


# ---------------------------------------------------------------------------
# Monolith split tests (v0.25, audit T14)
# ---------------------------------------------------------------------------

class ModuleSplitTests(unittest.TestCase):
    """dual_chat.py is split into focused sibling modules. These tests
    pin the split itself: every module imports standalone, dual_chat
    re-exports the moved names (the same objects, not copies), and
    each module's moved code still behaves. The full behavior is
    covered by the existing tests, which import through dual_chat
    unchanged."""

    MODULES = ("duo_ollama", "duo_stats", "duo_text", "duo_tasks",
               "duo_markdown", "duo_model", "duo_transcript",
               "duo_storage", "duo_runner", "duo_icon", "duo_menu",
               "duo_deck")

    def _tree(self):
        return os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))

    def test_each_module_imports_standalone(self):
        import importlib
        for name in self.MODULES:
            module = importlib.import_module(name)
            self.assertIsNotNone(module, name)

    def test_each_module_file_exists_in_the_tree(self):
        for name in self.MODULES:
            path = os.path.join(self._tree(), name + ".py")
            self.assertTrue(os.path.isfile(path), path)

    def test_dual_chat_is_no_longer_a_monolith(self):
        # 5,165 lines before the split. If the window genuinely grows
        # past this, update the bound -- but a jump back toward the
        # monolith means the split is being undone.
        path = os.path.join(self._tree(), "dual_chat.py")
        with open(path, encoding="utf-8") as fh:
            count = sum(1 for _ in fh)
        self.assertLess(count, 4000, count)

    def test_reexports_are_the_same_objects(self):
        import dual_chat
        import duo_deck
        import duo_icon
        import duo_markdown
        import duo_menu
        import duo_model
        import duo_ollama
        import duo_runner
        import duo_stats
        import duo_storage
        import duo_tasks
        import duo_text
        import duo_transcript
        pairs = [
            (dual_chat.make_app_icon, duo_icon.make_app_icon),
            (dual_chat.build_menu_bar, duo_menu.build_menu_bar),
            (dual_chat.DeckServer, duo_deck.DeckServer),
            (dual_chat.build_deck_state, duo_deck.build_deck_state),
            (dual_chat.get_lan_ip, duo_deck.get_lan_ip),
            (dual_chat.render_qr_pixmap, duo_deck.render_qr_pixmap),
            (dual_chat.markdown_to_html, duo_markdown.markdown_to_html),
            (dual_chat.ChatSide, duo_model.ChatSide),
            (dual_chat.ChatMessage, duo_model.ChatMessage),
            (dual_chat.Conversation, duo_model.Conversation),
            (dual_chat.NoModelSelected, duo_model.NoModelSelected),
            (dual_chat.OllamaClient, duo_ollama.OllamaClient),
            (dual_chat.OllamaError, duo_ollama.OllamaError),
            (dual_chat.Cancelled, duo_ollama.Cancelled),
            (dual_chat.CHAT_RETRY_DELAYS, duo_ollama.CHAT_RETRY_DELAYS),
            (dual_chat.check_for_update, duo_ollama.check_for_update),
            (dual_chat.parse_version, duo_ollama.parse_version),
            (dual_chat.SerialRunner, duo_runner.SerialRunner),
            (dual_chat.compute_turn_stats, duo_stats.compute_turn_stats),
            (dual_chat.format_stats_line, duo_stats.format_stats_line),
            (dual_chat.read_system_meter, duo_stats.read_system_meter),
            (dual_chat.format_system_meter,
             duo_stats.format_system_meter),
            (dual_chat.write_text_atomic, duo_storage.write_text_atomic),
            (dual_chat.save_pair, duo_storage.save_pair),
            (dual_chat.ConversationError, duo_storage.ConversationError),
            (dual_chat.TaskSession, duo_tasks.TaskSession),
            (dual_chat.phrase_matches, duo_text.phrase_matches),
            (dual_chat.format_side_transcript,
             duo_transcript.format_side_transcript),
            (dual_chat.parse_side_transcript,
             duo_transcript.parse_side_transcript),
            (dual_chat.TranscriptError, duo_transcript.TranscriptError),
        ]
        for through_dual_chat, original in pairs:
            self.assertIs(through_dual_chat, original, original)

    def test_moved_code_still_behaves(self):
        import duo_markdown
        import duo_model
        import duo_ollama
        import duo_stats
        import duo_storage
        import duo_tasks
        import duo_text
        import duo_transcript
        # Ollama payload parsing sorts names.
        self.assertEqual(
            duo_ollama.parse_tags_payload(
                {"models": [{"name": "b"}, {"name": "a"}]}),
            ["a", "b"])
        # Stats math: 40 tokens over 2s is 20 tok/s.
        stats = duo_stats.compute_turn_stats(
            {"eval_count": 40, "eval_duration_ns": 2_000_000_000,
             "prompt_eval_count": 10, "ttft_s": 0.5, "total_s": 2.5})
        self.assertEqual(stats["tok_per_sec"], 20.0)
        self.assertEqual(duo_stats.format_stats_line(None),
                         duo_stats.STATS_EMPTY_LINE)
        # Whole-word matching: "agreed" is not "disagreed".
        self.assertTrue(duo_text.phrase_matches("agreed", "I agreed."))
        self.assertFalse(
            duo_text.phrase_matches("agreed", "I disagreed."))
        self.assertEqual(duo_text.parse_stop_phrases("a, b ,"),
                         ["a", "b"])
        # Tasks: "nothing" is always one round.
        self.assertEqual(
            duo_tasks.TaskSession("A", "nothing", rounds=5).rounds, 1)
        # Markdown: bold renders, raw HTML shows as text.
        self.assertIn("<b>hi</b>",
                      duo_markdown.markdown_to_html("**hi**"))
        self.assertIn("&lt;b&gt;",
                      duo_markdown.markdown_to_html("<b>hi</b>"))
        # Model: a side holds one list of messages, each with its
        # role, content, label, and display text together.
        side = duo_model.ChatSide("A")
        side.add_user("hello", display="(short)")
        self.assertEqual(side.display_text(0), "(short)")
        self.assertEqual(sorted(duo_model.Conversation().sides),
                         ["A", "B"])
        # Transcripts: an empty log exports "No messages yet.", and
        # a one-message export parses back.
        self.assertIn("No messages yet.",
                      duo_transcript.format_full_transcript([]))
        exported = duo_transcript.format_side_transcript(side)
        entries = duo_transcript.parse_side_transcript(exported)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["label"], "You")
        # Storage: atomic writes replace, presets validate.
        folder = tempfile.mkdtemp(prefix="duo-split-")
        try:
            path = os.path.join(folder, "out.txt")
            duo_storage.write_text_atomic(path, "one")
            duo_storage.write_text_atomic(path, "two")
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "two")
            self.assertFalse(duo_storage.is_valid_preset({}))
            self.assertFalse(
                duo_storage.presets_file_corrupt(
                    os.path.join(folder, "missing.json")))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_serial_runner_still_runs_jobs_in_order(self):
        import duo_runner
        done = []
        finished = threading.Event()

        def on_done(job, text):
            done.append((job["id"], text))
            if len(done) == 2:
                finished.set()

        runner = duo_runner.SerialRunner(on_done=on_done)
        runner.submit("first", lambda emit, ev: "one")
        runner.submit("second", lambda emit, ev: "two")
        self.assertTrue(finished.wait(timeout=10))
        self.assertEqual(done, [("first", "one"), ("second", "two")])


# ---------------------------------------------------------------------------
# One message structure + MIT license (v0.26, audit T15)
# ---------------------------------------------------------------------------

class MessageStructureTests(unittest.TestCase):
    """ChatSide keeps one list of ChatMessage objects -- role,
    content, label, and display text together per message -- instead
    of the three parallel arrays (history, labels, display) the
    refactor replaced. The on-disk conversation format is unchanged:
    a file saved by an older version still loads, field for field."""

    def _tree(self):
        return os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))

    def test_one_message_holds_all_four_fields(self):
        side = ChatSide("A")
        side.model = "fake-a:latest"
        side.add_user("File: notes.txt\nsecret contents",
                      label="You", display="(Attached: notes.txt)")
        side.add_assistant("noted")
        self.assertEqual(len(side.messages), 2)
        first, second = side.messages
        self.assertEqual((first.role, first.content, first.label,
                          first.display),
                         ("user", "File: notes.txt\nsecret contents",
                          "You", "(Attached: notes.txt)"))
        self.assertEqual((second.role, second.content, second.label,
                          second.display),
                         ("assistant", "noted", "fake-a:latest", None))
        # The parallel arrays are gone: one list is the whole state.
        self.assertFalse(hasattr(side, "history"))
        self.assertFalse(hasattr(side, "labels"))
        self.assertFalse(hasattr(side, "display"))

    def test_add_assistant_label_defaults(self):
        side = ChatSide("B")
        side.add_assistant("hello")
        self.assertEqual(side.messages[0].label, "Assistant")
        side.model = "fake-b:latest"
        side.add_assistant("again")
        self.assertEqual(side.messages[1].label, "fake-b:latest")
        side.add_assistant("named", label="Custom")
        self.assertEqual(side.messages[2].label, "Custom")

    def test_display_text_comes_from_the_message(self):
        msg = ChatMessage("user", "full text", "You", "(short)")
        self.assertEqual(msg.display_text(), "(short)")
        msg2 = ChatMessage("assistant", "full text", "model")
        self.assertIsNone(msg2.display)
        self.assertEqual(msg2.display_text(), "full text")

    def test_move_keeps_all_fields_together(self):
        # The point of the refactor: reordering moves the whole
        # message, so a label or display text can never be left
        # behind on the wrong entry the way parallel arrays could.
        side = ChatSide("A")
        side.add_user("one", display="(1)")
        side.add_user("two", label="You [task]", display="(2)")
        side.add_assistant("three")
        side.move_up(1)
        self.assertEqual(
            [(m.content, m.label, m.display) for m in side.messages],
            [("two", "You [task]", "(2)"),
             ("one", "You", "(1)"),
             ("three", "Assistant", None)])
        side.delete_at(0)
        self.assertEqual(
            [(m.content, m.label, m.display) for m in side.messages],
            [("one", "You", "(1)"),
             ("three", "Assistant", None)])

    def test_summarize_keeps_fields_together(self):
        side = ChatSide("A")
        side.model = "fake-a:latest"
        for i in range(6):
            side.add_user("q%d" % i, display="(q%d)" % i)
            side.add_assistant("a%d" % i)
        self.assertTrue(side.summarize("they talked"))
        kept = side.messages[1:]
        self.assertEqual(len(kept), 4)
        self.assertEqual(kept[0].content, "q4")
        self.assertEqual(kept[0].display, "(q4)")
        self.assertEqual(kept[0].label, "You")
        self.assertEqual(side.messages[0].label, SUMMARY_LABEL)

    def test_conversation_file_format_unchanged(self):
        # What hits the disk is still the v0.17 shape: entries are
        # plain dicts with role, content, label, display.
        convo = Conversation()
        convo.sides["A"].model = "fake-a:latest"
        convo.sides["A"].add_user("hello", display="(short)")
        convo.sides["A"].add_assistant("hi")
        data = conversation_to_data(convo, [])
        self.assertEqual(data["version"], CONVERSATION_FORMAT_VERSION)
        self.assertEqual(data["sides"]["A"]["entries"], [
            {"role": "user", "content": "hello", "label": "You",
             "display": "(short)"},
            {"role": "assistant", "content": "hi",
             "label": "fake-a:latest", "display": None}])

    def test_old_format_file_loads_into_the_new_structure(self):
        # A file written by hand in the v0.17 shape (the way older
        # versions saved it) loads with every field exact.
        data = {
            "version": 1,
            "sides": {
                "A": {
                    "model": "fake-a:latest",
                    "instruction": "Be brief.",
                    "entries": [
                        {"role": "user", "content": "File: n.txt\nbody",
                         "label": "You", "display": "(Attached: n.txt)"},
                        {"role": "assistant", "content": "read it",
                         "label": "fake-a:latest", "display": None},
                        {"role": "user",
                         "content": "Earlier in this conversation: x",
                         "label": SUMMARY_LABEL, "display": None},
                    ],
                },
                "B": {
                    "model": None,
                    "instruction": "",
                    "entries": [],
                },
            },
            "log": [["A", "You", "(Attached: n.txt)"]],
        }
        convo, log = data_to_conversation(data)
        side = convo.sides["A"]
        self.assertEqual(side.model, "fake-a:latest")
        self.assertEqual(side.instruction, "Be brief.")
        self.assertEqual(side.messages, [
            ChatMessage("user", "File: n.txt\nbody", "You",
                        "(Attached: n.txt)"),
            ChatMessage("assistant", "read it", "fake-a:latest"),
            ChatMessage("user", "Earlier in this conversation: x",
                        SUMMARY_LABEL),
        ])
        self.assertEqual(side.messages[0].display_text(),
                         "(Attached: n.txt)")
        self.assertEqual(log, [("A", "You", "(Attached: n.txt)")])
        self.assertEqual(convo.sides["B"].messages, [])

    def test_old_format_file_loads_from_disk(self):
        folder = tempfile.mkdtemp(prefix="duo-t15-")
        try:
            path = os.path.join(folder, "old.json")
            data = {
                "version": 1,
                "sides": {
                    "A": {"model": "fake-a:latest", "instruction": "",
                          "entries": [{"role": "user", "content": "hi",
                                       "label": "You", "display": None}]},
                    "B": {"model": None, "instruction": "",
                          "entries": []},
                },
                "log": [],
            }
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            convo, _log = load_conversation_file(path)
            self.assertEqual(convo.sides["A"].messages,
                             [ChatMessage("user", "hi", "You")])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_license_file_is_mit(self):
        path = os.path.join(self._tree(), "LICENSE")
        self.assertTrue(os.path.isfile(path), path)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2026 Joshua Lofland", text)
        self.assertIn("Permission is hereby granted, free of charge",
                      text)

    def test_readme_says_mit(self):
        with open(os.path.join(self._tree(), "README.md"),
                  encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("License: MIT", readme)
        self.assertNotIn("not decided yet", readme)

    def test_zip_includes_the_license(self):
        with open(os.path.join(self._tree(), "build_zip.py"),
                  encoding="utf-8") as fh:
            script = fh.read()
        self.assertIn('"LICENSE": 0o100644', script)

    def test_zip_includes_the_menu_module(self):
        with open(os.path.join(self._tree(), "build_zip.py"),
                  encoding="utf-8") as fh:
            script = fh.read()
        self.assertIn('"duo_menu.py": 0o100644', script)

    def test_zip_includes_the_deck_module(self):
        with open(os.path.join(self._tree(), "build_zip.py"),
                  encoding="utf-8") as fh:
            script = fh.read()
        self.assertIn('"duo_deck.py": 0o100644', script)

class UpdateCheckTests(unittest.TestCase):
    """v0.28 (audit T17): the update check's pure core -- version
    parsing and comparison, and the GitHub latest-release lookup
    against stubbed HTTP responses. The UI notes the outcomes map
    to are pinned in UpdateCheckGuiTests."""

    def _open(self, body=None, error=None, calls=None):
        """A urlopen stub: answers with `body` bytes, raises `error`,
        and records (request, timeout) pairs in `calls`."""

        class _Response:
            def read(self):
                return body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def opener(request, timeout=None):
            if calls is not None:
                calls.append((request, timeout))
            if error is not None:
                raise error
            return _Response()

        return opener

    def _tag_body(self, tag):
        return json.dumps({"tag_name": tag}).encode("utf-8")

    def test_parse_version_plain(self):
        self.assertEqual(parse_version("0.28"), (0, 28))
        self.assertEqual(parse_version("1.2.3"), (1, 2, 3))

    def test_parse_version_leading_v(self):
        self.assertEqual(parse_version("v0.28"), (0, 28))
        self.assertEqual(parse_version("V1.0"), (1, 0))

    def test_parse_version_compares_numerically(self):
        # "0.9" < "0.10" must hold -- a string compare gets this
        # backwards, and a wrong order would hide a real update.
        self.assertLess(parse_version("0.9"), parse_version("0.10"))
        self.assertLess(parse_version("0.28"), parse_version("0.29"))
        self.assertEqual(parse_version("0.28"), parse_version("v0.28"))

    def test_parse_version_malformed_is_none(self):
        for bad in ("", "v", "banana", "0.28-beta", "1.", ".5",
                    "0.-1", "0. 28", None, 5, b"0.28"):
            self.assertIsNone(parse_version(bad), bad)

    def test_newer_tag_reports_newer_with_normalized_version(self):
        result = check_for_update(
            "0.28", urlopen=self._open(self._tag_body("v0.29")))
        self.assertEqual(result, ("newer", "0.29"))

    def test_tag_without_v_reports_newer(self):
        result = check_for_update(
            "0.28", urlopen=self._open(self._tag_body("0.30")))
        self.assertEqual(result, ("newer", "0.30"))

    def test_same_tag_reports_latest(self):
        result = check_for_update(
            "0.28", urlopen=self._open(self._tag_body("v0.28")))
        self.assertEqual(result, ("latest", None))

    def test_older_tag_reports_latest(self):
        result = check_for_update(
            "0.28", urlopen=self._open(self._tag_body("v0.27")))
        self.assertEqual(result, ("latest", None))

    def test_nine_is_older_than_ten(self):
        result = check_for_update(
            "0.9", urlopen=self._open(self._tag_body("v0.10")))
        self.assertEqual(result, ("newer", "0.10"))

    def test_malformed_json_is_failed(self):
        result = check_for_update(
            "0.28", urlopen=self._open(b"not json at all"))
        self.assertEqual(result, ("failed", None))

    def test_missing_tag_is_failed(self):
        body = json.dumps({"name": "no tag here"}).encode("utf-8")
        result = check_for_update("0.28", urlopen=self._open(body))
        self.assertEqual(result, ("failed", None))

    def test_non_dict_payload_is_failed(self):
        body = json.dumps(["v0.29"]).encode("utf-8")
        result = check_for_update("0.28", urlopen=self._open(body))
        self.assertEqual(result, ("failed", None))

    def test_malformed_tag_is_failed(self):
        result = check_for_update(
            "0.28", urlopen=self._open(self._tag_body("banana")))
        self.assertEqual(result, ("failed", None))

    def test_timeout_is_failed(self):
        result = check_for_update(
            "0.28", urlopen=self._open(error=TimeoutError("timed out")))
        self.assertEqual(result, ("failed", None))

    def test_connection_error_is_failed(self):
        result = check_for_update(
            "0.28", urlopen=self._open(
                error=urllib.error.URLError("no route to host")))
        self.assertEqual(result, ("failed", None))

    def test_http_error_is_failed(self):
        error = urllib.error.HTTPError(
            UPDATE_CHECK_URL, 403, "Forbidden", {}, None)
        result = check_for_update("0.28", urlopen=self._open(error=error))
        self.assertEqual(result, ("failed", None))

    def test_unexpected_error_never_raises(self):
        result = check_for_update(
            "0.28", urlopen=self._open(error=RuntimeError("boom")))
        self.assertEqual(result, ("failed", None))

    def test_bad_current_version_is_failed(self):
        result = check_for_update(
            "banana", urlopen=self._open(self._tag_body("v0.29")))
        self.assertEqual(result, ("failed", None))

    def test_asks_the_github_latest_release_url_with_short_timeout(self):
        calls = []
        check_for_update("0.28", urlopen=self._open(
            self._tag_body("v0.28"), calls=calls))
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(request.full_url, UPDATE_CHECK_URL)
        self.assertEqual(timeout, UPDATE_CHECK_TIMEOUT)
        self.assertEqual(UPDATE_CHECK_TIMEOUT, 5)

    def test_default_url_is_the_repo_latest_release(self):
        self.assertEqual(
            UPDATE_CHECK_URL,
            "https://api.github.com/repos/BROKE-N-BORED/DUO"
            "/releases/latest")

    def test_env_override_changes_the_url(self):
        override = "http://127.0.0.1:9/fake-releases"
        calls = []
        with mock.patch.dict(os.environ,
                             {UPDATE_CHECK_URL_ENV: override}):
            check_for_update("0.28", urlopen=self._open(
                self._tag_body("v0.28"), calls=calls))
        self.assertEqual(calls[0][0].full_url, override)


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class UpdateCheckGuiTests(unittest.TestCase):
    """v0.28 (audit T17): the update check in the window -- the
    startup check and the button, with the network stubbed. The
    stub is patched in before build_window, because the window
    runs its startup check as soon as it is built."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        self.check_result = ("latest", None)
        self.check_first_result = None
        self.check_gate = None
        self.check_calls = []
        self.window = None
        self._patcher = mock.patch("dual_chat.check_for_update",
                                   side_effect=self._stub_check)
        self._patcher.start()
        self.client.reset()

    def tearDown(self):
        if self.check_gate is not None:
            self.check_gate.set()
        if self.window is not None:
            self.window.close()
        self.app.processEvents()
        self._patcher.stop()

    def _stub_check(self, *args, **kwargs):
        call_no = len(self.check_calls)
        self.check_calls.append(args)
        # The gate holds only the first (startup) call, so a test
        # can release it after the window has settled.
        if self.check_gate is not None and call_no == 0:
            self.check_gate.wait(timeout=10)
        if call_no == 0 and self.check_first_result is not None:
            return self.check_first_result
        return self.check_result

    def _build_window(self):
        from dual_chat import build_window
        self.window = build_window(self.client, logger=TEST_LOGGER)
        self.window.show()
        self.state = self.window._dual_chat
        self.assertTrue(self._wait_for(
            lambda: self.state["widgets"]["A"]["picker"].count() == 2),
            "pickers should load the fake models")

    def _wait_for(self, cond, timeout=10.0):
        from PySide6.QtTest import QTest
        deadline = time.time() + timeout
        while time.time() < deadline:
            QTest.qWait(50)
            if cond():
                return True
        return cond()

    def _message(self):
        return self.window.statusBar().currentMessage()

    def test_closing_while_a_check_runs_waits_for_it(self):
        # v0.31: destroying a running QThread aborts the whole
        # process ("QThread: Destroyed while thread '' is still
        # running", exit 134 in the full suite). Closing the
        # window while the startup check is still in flight must
        # wait for it to finish instead.
        import threading
        self.check_gate = threading.Event()
        self._build_window()
        checkers = self.state["update_checkers"]
        self.assertTrue(self._wait_for(
            lambda: len(checkers) == 1
            and checkers[0].thread.isRunning(),
            timeout=10),
            "the startup check should be running, held on the gate")
        # Release the gate from another thread: the GUI thread is
        # about to block inside close() while it waits for the
        # checker, so nothing on the GUI thread could release it.
        # Hold the checker itself: close() cleans it out of the
        # list once its thread finishes.
        checker = checkers[0]
        threading.Timer(0.5, self.check_gate.set).start()
        self.window.close()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(checker.thread.isFinished())
        self.assertTrue(self._wait_for(lambda: checkers == []),
                        "the finished checker should be cleaned up")

    def test_button_present_and_enabled_at_idle(self):
        self._build_window()
        btn = self.state["update_btn"]
        self.assertTrue(btn.isVisible())
        self.assertTrue(btn.isEnabled())
        self.assertEqual(btn.text(), "Check for updates")

    def test_startup_check_newer_shows_the_note(self):
        # Hold the startup check until the model list has loaded:
        # otherwise populate_models' "Ollama connected" note can
        # land after the update note and hide it.
        self.check_gate = threading.Event()
        self.check_first_result = ("newer", "0.29")
        self._build_window()
        self.assertEqual(self._message(),
                         "Ollama connected: 2 model(s) found.")
        self.check_gate.set()
        self.assertTrue(self._wait_for(
            lambda: self._message()
            == MSG_UPDATE_AVAILABLE % "0.29"), self._message())

    def test_startup_check_latest_stays_silent(self):
        self.check_first_result = ("latest", None)
        self._build_window()
        self.assertTrue(self._wait_for(
            lambda: len(self.check_calls) >= 1))
        self.app.processEvents()
        self.assertEqual(self._message(),
                         "Ollama connected: 2 model(s) found.")

    def test_startup_check_failed_stays_silent(self):
        self.check_first_result = ("failed", None)
        self._build_window()
        self.assertTrue(self._wait_for(
            lambda: len(self.check_calls) >= 1))
        self.app.processEvents()
        self.assertEqual(self._message(),
                         "Ollama connected: 2 model(s) found.")

    def test_manual_check_latest(self):
        self._build_window()
        self.assertTrue(self._wait_for(
            lambda: len(self.check_calls) >= 1))
        self.check_result = ("latest", None)
        self.state["update_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: self._message() == MSG_UPDATE_LATEST),
            self._message())

    def test_manual_check_failed(self):
        self._build_window()
        self.assertTrue(self._wait_for(
            lambda: len(self.check_calls) >= 1))
        self.check_result = ("failed", None)
        self.state["update_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: self._message() == MSG_UPDATE_FAILED),
            self._message())

    def test_manual_check_newer(self):
        self._build_window()
        self.assertTrue(self._wait_for(
            lambda: len(self.check_calls) >= 1))
        self.check_result = ("newer", "0.31")
        self.state["update_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: self._message()
            == MSG_UPDATE_AVAILABLE % "0.31"), self._message())

    def test_manual_check_while_startup_in_flight_last_wins(self):
        # The startup check is held on the gate; the manual check
        # runs and reports first. When the startup check finally
        # finishes, its note is the one standing -- no stale
        # result is left over the newer one.
        self.check_gate = threading.Event()
        self.check_first_result = ("newer", "0.30")
        self._build_window()
        self.check_result = ("latest", None)
        self.state["update_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: self._message() == MSG_UPDATE_LATEST),
            self._message())
        self.check_gate.set()
        self.assertTrue(self._wait_for(
            lambda: self._message()
            == MSG_UPDATE_AVAILABLE % "0.30"), self._message())


# ---------------------------------------------------------------------------
# Activity lights + system meter (v0.29)
# ---------------------------------------------------------------------------

class SystemMeterTests(unittest.TestCase):
    """The /proc parsing behind the status-bar meter: CPU % from the
    delta between two /proc/stat samples, RAM used/total from
    /proc/meminfo. Pure functions, no Qt, no real /proc needed --
    the tests feed fake file contents."""

    STAT_ONE = ("cpu  100 0 50 800 50 0 0 0 0 0\n"
                "cpu0 100 0 50 800 50 0 0 0 0 0\n")
    STAT_TWO = ("cpu  200 0 50 900 50 0 0 0 0 0\n"
                "cpu0 200 0 50 900 50 0 0 0 0 0\n")
    MEMINFO = ("MemTotal:       8000000 kB\n"
               "MemFree:        1000000 kB\n"
               "MemAvailable:   2000000 kB\n")

    def test_parse_cpu_times_reads_the_aggregate_line(self):
        # idle is idle + iowait (800 + 50); total is every field.
        self.assertEqual(parse_cpu_times(self.STAT_ONE), (850, 1000))
        self.assertEqual(parse_cpu_times(self.STAT_TWO), (950, 1200))

    def test_parse_cpu_times_rejects_bad_input(self):
        self.assertIsNone(parse_cpu_times("cpu0 1 2 3 4 5\n"))
        self.assertIsNone(parse_cpu_times("cpu  a b c d e\n"))
        self.assertIsNone(parse_cpu_times("cpu  1 2 3\n"))
        self.assertIsNone(parse_cpu_times(""))

    def test_cpu_percent_between_is_the_delta(self):
        # idle grew 100 of a 200-tick delta: half the time busy.
        self.assertEqual(
            cpu_percent_between((850, 1000), (950, 1200)), 50.0)
        # All busy: idle flat while total grows.
        self.assertEqual(
            cpu_percent_between((850, 1000), (850, 1200)), 100.0)

    def test_cpu_percent_between_rejects_degenerate_samples(self):
        self.assertIsNone(cpu_percent_between(None, (950, 1200)))
        self.assertIsNone(cpu_percent_between((850, 1000), None))
        self.assertIsNone(cpu_percent_between((850, 1000), (850, 1000)))

    def test_cpu_percent_between_clamps_a_counter_reset(self):
        # Idle fell while total grew: the math says 150%, which is
        # nonsense -- clamp to 100 rather than show a wild number.
        self.assertEqual(
            cpu_percent_between((900, 1000), (850, 1100)), 100.0)

    def test_parse_meminfo(self):
        total, available = parse_meminfo(self.MEMINFO)
        self.assertEqual(total, 8000000 * 1024)
        self.assertEqual(available, 2000000 * 1024)

    def test_parse_meminfo_rejects_bad_input(self):
        self.assertIsNone(parse_meminfo("MemTotal: 8000000 kB\n"))
        self.assertIsNone(parse_meminfo("MemAvailable: 2000000 kB\n"))
        self.assertIsNone(parse_meminfo("MemTotal: lots kB\n"
                                        "MemAvailable: 2000000 kB\n"))

    def test_format_system_meter(self):
        self.assertEqual(
            format_system_meter(42.4, 3100000000, 7700000000),
            "CPU 42% · RAM 3.1 / 7.7 GB")
        # First read: no CPU delta yet, RAM known.
        self.assertEqual(
            format_system_meter(None, 3100000000, 7700000000),
            "CPU -- · RAM 3.1 / 7.7 GB")
        # /proc/stat readable, /proc/meminfo not.
        self.assertEqual(
            format_system_meter(50.0, None, None), "CPU 50% · RAM --")
        # Nothing known at all: empty, not a row of dashes.
        self.assertEqual(format_system_meter(None, None, None), "")
        # Small machines get MB.
        self.assertEqual(
            format_system_meter(10.0, 256000000, 512000000),
            "CPU 10% · RAM 256 / 512 MB")

    def _write(self, folder, name, text):
        path = os.path.join(folder, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_read_system_meter_first_read_is_the_baseline(self):
        folder = tempfile.mkdtemp(prefix="duo-meter-")
        try:
            stat = self._write(folder, "stat", self.STAT_ONE)
            mem = self._write(folder, "meminfo", self.MEMINFO)
            cpu, used, total, prev = read_system_meter(
                None, stat_path=stat, meminfo_path=mem)
            # No previous sample, so no % -- but RAM is known now.
            self.assertIsNone(cpu)
            self.assertEqual(total, 8000000 * 1024)
            self.assertEqual(used, 6000000 * 1024)
            self.assertEqual(prev, (850, 1000))
            # Second read, against a busier stat file: a real %.
            stat2 = self._write(folder, "stat2", self.STAT_TWO)
            cpu, _, _, _ = read_system_meter(
                prev, stat_path=stat2, meminfo_path=mem)
            self.assertEqual(cpu, 50.0)
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_read_system_meter_unreadable_proc_is_silent(self):
        folder = tempfile.mkdtemp(prefix="duo-meter-")
        try:
            missing_stat = os.path.join(folder, "no-stat")
            missing_mem = os.path.join(folder, "no-meminfo")
            cpu, used, total, prev = read_system_meter(
                (850, 1000), stat_path=missing_stat,
                meminfo_path=missing_mem)
            self.assertIsNone(cpu)
            self.assertIsNone(used)
            self.assertIsNone(total)
            # The previous sample survives a failed read, so the
            # next good read still has a baseline.
            self.assertEqual(prev, (850, 1000))
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class ActivityStateTests(unittest.TestCase):
    """The side-state table itself: every state has a color and
    plain words, and the error light's hold is the value the UI
    promises (5 seconds)."""

    def test_side_states_table_covers_every_state(self):
        self.assertEqual(
            sorted(SIDE_STATES),
            ["error", "idle", "queued", "review", "summarizing",
             "thinking", "waiting"])
        for name, (color, text) in SIDE_STATES.items():
            self.assertTrue(color.startswith("#"), name)
            self.assertTrue(text, name)
        self.assertEqual(SIDE_STATES["idle"], ("#8a8a8a", "Idle"))
        self.assertEqual(SIDE_STATES["thinking"],
                         ("#2fbf5a", "Thinking…"))
        self.assertEqual(SIDE_STATES["waiting"],
                         ("#e8a33d", "Waiting for Ollama…"))
        self.assertEqual(SIDE_STATES["review"],
                         ("#e8a33d", "Waiting for you to review…"))
        self.assertEqual(SIDE_STATES["error"],
                         ("#d64545", "Something went wrong"))

    def test_error_hold_is_five_seconds(self):
        self.assertEqual(ERROR_STATE_HOLD_MS, 5000)


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class ActivityIndicatorGuiTests(unittest.TestCase):
    """v0.29: the per-side light + activity words follow the job
    lifecycle, and the status-bar meter shows real numbers. Light
    colors are asserted through the stylesheet the code sets, not
    pixels."""

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
        self.app.processEvents()

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

    def _side_state(self, side):
        return self.state["side_state"][side]

    def _dot_color(self, side):
        # The stylesheet carries the color as a hex string.
        sheet = self.state["widgets"][side]["activity_dot"].styleSheet()
        return sheet.split("background-color:")[1].split(";")[0].strip()

    def _activity_text(self, side):
        return self.state["widgets"][side]["activity"].text()

    def _assert_side(self, side, state):
        color, text = SIDE_STATES[state]
        self.assertEqual(self._side_state(side), state)
        self.assertEqual(self._dot_color(side), color)
        self.assertEqual(self._activity_text(side), text)
        # The bottom-row status label shows the same words.
        self.assertEqual(
            self.state["widgets"][side]["status"].text(), text)

    def _send_to_a_and_wait(self, text, timeout=15.0):
        self.state["composer"].setPlainText(text)
        self.state["send_a"].click()
        return self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=timeout)

    def test_both_sides_start_idle(self):
        self._assert_side("A", "idle")
        self._assert_side("B", "idle")

    def test_send_shows_thinking_then_idle(self):
        self.client.delay = 0.3
        self.client.replies = {"fake-a:latest": [
            "one two three four five six"]}
        self.state["composer"].setPlainText("slow one")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(
            lambda: self._side_state("A") == "thinking", timeout=5))
        self._assert_side("A", "thinking")
        # The other side never moved.
        self._assert_side("B", "idle")
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self._assert_side("A", "idle")

    def test_waiting_before_first_chunk(self):
        from dual_chat import build_window

        gate = threading.Event()

        class BlockingClient(FakeGuiClient):
            def chat_stream(self, model, messages, on_chunk,
                            cancel_event, timeout=1800, on_metrics=None,
                            on_retry=None, options=None):
                # Ollama has the job but hasn't answered yet.
                gate.wait(timeout=10)
                return super().chat_stream(
                    model, messages, on_chunk, cancel_event,
                    timeout=timeout, on_metrics=on_metrics,
                    on_retry=on_retry, options=options)

        window = build_window(BlockingClient(), logger=TEST_LOGGER)
        try:
            window.show()
            state = window._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state["widgets"]["A"]["picker"].count() == 2))
            state["widgets"]["A"]["picker"].setCurrentIndex(0)
            self.app.processEvents()
            state["composer"].setPlainText("hello")
            state["send_a"].click()
            self.assertTrue(self._wait_for(
                lambda: state["side_state"]["A"] == "waiting",
                timeout=5))
            color, text = SIDE_STATES["waiting"]
            self.assertEqual(
                state["widgets"]["A"]["activity"].text(), text)
            self.assertIn(color, state["widgets"]["A"]
                          ["activity_dot"].styleSheet())
            gate.set()
            self.assertTrue(self._wait_for(
                lambda: state["side_state"]["A"] == "thinking",
                timeout=5))
            self.assertTrue(self._wait_for(
                lambda: not state["state"]["busy"], timeout=15))
            self.assertEqual(state["side_state"]["A"], "idle")
        finally:
            gate.set()
            window.close()

    def test_queued_side_shows_amber_while_other_thinks(self):
        self.client.delay = 0.3
        self.client.replies = {"fake-a:latest": [
            "one two three four five six"],
            "fake-b:latest": ["seven eight nine"]}
        self.state["composer"].setPlainText("both, slow")
        self.state["send_both"].click()
        # A starts first; B waits behind it, amber.
        self.assertTrue(self._wait_for(
            lambda: self._side_state("A") == "thinking"
            and self._side_state("B") == "queued", timeout=5))
        self._assert_side("B", "queued")
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self._assert_side("A", "idle")
        self._assert_side("B", "idle")

    def test_failed_turn_shows_error_then_idle(self):
        from dual_chat import build_window

        class FailingClient(FakeGuiClient):
            def chat_stream(self, model, messages, on_chunk,
                            cancel_event, timeout=1800, on_metrics=None,
                            on_retry=None, options=None):
                on_chunk("partial ")
                raise OllamaError(MSG_MODEL_FAILED)

        # The error hold is 5s in the product; the window under test
        # uses a short hold so the suite doesn't wait it out. The
        # real value is pinned by ActivityStateTests.
        with mock.patch("dual_chat.ERROR_STATE_HOLD_MS", 200):
            window = build_window(FailingClient(),
                                  logger=TEST_LOGGER)
            try:
                window.show()
                state = window._dual_chat
                self.assertTrue(self._wait_for(
                    lambda: state["widgets"]["A"]["picker"].count() == 2))
                state["widgets"]["A"]["picker"].setCurrentIndex(0)
                self.app.processEvents()
                state["composer"].setPlainText("hello")
                state["send_a"].click()
                self.assertTrue(self._wait_for(
                    lambda: state["side_state"]["A"] == "error",
                    timeout=10))
                color, text = SIDE_STATES["error"]
                self.assertEqual(
                    state["widgets"]["A"]["activity"].text(), text)
                self.assertIn(color, state["widgets"]["A"]
                              ["activity_dot"].styleSheet())
                # The red light falls back to idle on its own.
                self.assertTrue(self._wait_for(
                    lambda: state["side_state"]["A"] == "idle",
                    timeout=5))
            finally:
                window.close()

    def test_new_job_overrides_error_light(self):
        from dual_chat import build_window

        class FailOnceClient(FakeGuiClient):
            failed = False

            def chat_stream(self, model, messages, on_chunk,
                            cancel_event, timeout=1800, on_metrics=None,
                            on_retry=None, options=None):
                if not FailOnceClient.failed:
                    FailOnceClient.failed = True
                    raise OllamaError(MSG_MODEL_FAILED)
                return super().chat_stream(
                    model, messages, on_chunk, cancel_event,
                    timeout=timeout, on_metrics=on_metrics,
                    on_retry=on_retry, options=options)

        with mock.patch("dual_chat.ERROR_STATE_HOLD_MS", 200):
            window = build_window(FailOnceClient(),
                                  logger=TEST_LOGGER)
            try:
                window.show()
                state = window._dual_chat
                self.assertTrue(self._wait_for(
                    lambda: state["widgets"]["A"]["picker"].count() == 2))
                state["widgets"]["A"]["picker"].setCurrentIndex(0)
                self.app.processEvents()
                state["composer"].setPlainText("first")
                state["send_a"].click()
                self.assertTrue(self._wait_for(
                    lambda: state["side_state"]["A"] == "error",
                    timeout=10))
                # A new send right away: the error light gives way
                # to the new turn, and the hold timer never drags
                # the side back to anything wrong afterwards.
                state["composer"].setPlainText("second")
                state["send_a"].click()
                self.assertTrue(self._wait_for(
                    lambda: state["side_state"]["A"] == "thinking",
                    timeout=5))
                self.assertTrue(self._wait_for(
                    lambda: not state["state"]["busy"], timeout=15))
                self.assertEqual(state["side_state"]["A"], "idle")
                self.assertTrue(self._wait_for(
                    lambda: state["side_state"]["A"] == "idle",
                    timeout=2))
            finally:
                window.close()

    def test_relay_review_pause_shows_waiting(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self.state["review_check"].setChecked(True)
        self.state["start_picker"].setCurrentIndex(0)
        self.state["cap_spin"].setValue(2)
        self.state["mode_picker"].setCurrentIndex(0)  # Blind
        self.state["composer"].setPlainText("Talk about fruit.")
        self.app.processEvents()
        self.state["relay_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["review_frame"].isVisible()
            and self.state["state"].get("pending_hop") is not None,
            timeout=15))
        # A's turn is done (idle); B waits on the user, amber.
        self._assert_side("A", "idle")
        self._assert_side("B", "review")
        self.state["review_forward"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["relay_status"].text().startswith(
                "Relay stopped"), timeout=15))
        self._assert_side("A", "idle")
        self._assert_side("B", "idle")

    def test_review_skip_clears_the_waiting_light(self):
        self.client.replies = {"fake-a:latest": ["apple pie"],
                               "fake-b:latest": ["banana split"]}
        self.state["review_check"].setChecked(True)
        self.state["start_picker"].setCurrentIndex(0)
        self.state["cap_spin"].setValue(2)
        self.state["mode_picker"].setCurrentIndex(0)  # Blind
        self.state["composer"].setPlainText("Talk about fruit.")
        self.app.processEvents()
        self.state["relay_btn"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["review_frame"].isVisible()
            and self.state["state"].get("pending_hop") is not None,
            timeout=15))
        self._assert_side("B", "review")
        self.state["review_skip"].click()
        self.assertTrue(self._wait_for(
            lambda: self.state["relay_status"].text().startswith(
                "Relay stopped"), timeout=15))
        # The relay ended from the pause: no stuck amber light.
        self._assert_side("B", "idle")

    def test_summarize_shows_summarizing(self):
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
            lambda: self._side_state("A") == "summarizing", timeout=5))
        self._assert_side("A", "summarizing")
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=15))
        self._assert_side("A", "idle")

    def test_cancelled_job_returns_to_idle(self):
        self.client.delay = 0.3
        self.client.replies = {"fake-a:latest": [
            "one two three four five six seven eight"]}
        self.state["composer"].setPlainText("slow one")
        self.state["send_a"].click()
        self.assertTrue(self._wait_for(
            lambda: self._side_state("A") == "thinking", timeout=5))
        self.state["widgets"]["A"]["stop"].click()
        self.assertTrue(self._wait_for(
            lambda: not self.state["state"]["busy"], timeout=10))
        # Stopped, not stuck: the light is back to grey idle.
        self._assert_side("A", "idle")

    def test_meter_label_shows_real_numbers(self):
        import re
        # The meter reads the real /proc here: after the second
        # tick (1s apart) both numbers are live.
        self.assertTrue(self._wait_for(
            lambda: re.match(
                r"CPU \d+% · RAM [\d.]+ / [\d.]+ (GB|MB)",
                self.state["meter_label"].text()) is not None,
            timeout=10), self.state["meter_label"].text())
        self.assertTrue(self.state["meter_timer"].isActive())

    def test_meter_timer_stops_when_the_window_closes(self):
        from dual_chat import build_window
        window = build_window(self.client, logger=TEST_LOGGER)
        timer = window._dual_chat["meter_timer"]
        self.assertTrue(timer.isActive())
        window.close()
        self.app.processEvents()
        # The timer is parented to the window: closing the window
        # destroys it, so it can never fire into a dead window.
        self.assertFalse(timer.isActive())


# ---------------------------------------------------------------------------
# Menu bar + keyboard shortcuts + How to Use index (v0.30, audit T18;
# U22 How to Use index, U33 no em-dashes in visible UI text)

class MenuBarTextTests(unittest.TestCase):
    """v0.30: the menu's plain-text pins -- the update notes use
    plain punctuation now, and no em-dash (literal or escaped) may
    appear in DUO's own code except the debug-report header, which
    mirrors the vendored debug_log layer's header word for word."""

    def _tree(self):
        return os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))

    def test_update_notes_use_plain_punctuation(self):
        self.assertEqual(
            MSG_UPDATE_AVAILABLE,
            "DUO %s is out. See the Releases page on GitHub.")
        self.assertEqual(
            MSG_UPDATE_FAILED,
            "Couldn't check for updates. Are you online?")

    def test_about_and_how_to_use_text_have_no_em_dash(self):
        import duo_menu
        self.assertNotIn("\u2014",
                         duo_menu.about_text(APP_VERSION))
        self.assertNotIn("\u2014", duo_menu.how_to_use_text())

    def test_how_to_use_index_has_the_nine_sections(self):
        import duo_menu
        headings = [heading for heading, _ in
                    duo_menu.HOW_TO_USE_SECTIONS]
        self.assertEqual(headings, [
            "The two panes", "Sending a message", "The relay",
            "Tasks", "Attaching files",
            "Transcripts and conversations",
            "Saved pairs and relay presets", "The phone deck",
            "The update check"])
        for _heading, body in duo_menu.HOW_TO_USE_SECTIONS:
            self.assertTrue(body.strip())

    def test_no_em_dash_in_product_code_except_report_header(self):
        import glob
        files = [os.path.join(self._tree(), "dual_chat.py"),
                 os.path.join(self._tree(), "duo_menu.py")]
        files += sorted(glob.glob(
            os.path.join(self._tree(), "duo_*.py")))
        # debug_log.py is vendored verbatim from the shared T21
        # layer (byte-identical by audit); its header stays.
        files = [path for path in files
                 if not path.endswith("debug_log.py")]
        for path in files:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
            for number, line in enumerate(lines, start=1):
                if "\u2014" in line or "\\u2014" in line:
                    self.assertIn(
                        "DEBUG REPORT", line,
                        "%s:%d: %s" % (path, number, line))


@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class MenuBarGuiTests(unittest.TestCase):
    """v0.30 (audit T18): the menu bar in the window -- File, Relay,
    and Help with the items and shortcuts pinned, every item calling
    the same handler as its button, and enabled states mirroring the
    buttons. The update check is stubbed, as in
    UpdateCheckGuiTests, so no test touches the network."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        from dual_chat import build_window
        self._patcher = mock.patch(
            "dual_chat.check_for_update",
            return_value=("latest", None))
        self._patcher.start()
        self.client.reset()
        self.tmp = tempfile.TemporaryDirectory()
        self.window = build_window(self.client, logger=TEST_LOGGER)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self._pick_models()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self._patcher.stop()
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

    def _send_both_and_wait(self, text):
        self.state["composer"].setPlainText(text)
        self.state["send_both"].click()
        return self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].messages) >= 2
            and len(self.state["convo"].sides["B"].messages) >= 2
            and not self.state["state"]["busy"]), timeout=15)

    def _save_dialog(self, path):
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getSaveFileName",
            return_value=(path, "Text files (*.txt)"))

    def _open_dialog(self, path):
        from PySide6 import QtWidgets
        return mock.patch.object(
            QtWidgets.QFileDialog, "getOpenFileName",
            return_value=(path, "Text files (*.txt)"))

    def _read(self, path):
        with open(path, encoding="utf-8") as fh:
            return fh.read()

    def _menus(self):
        menus = {}
        for action in self.window.menuBar().actions():
            menus[action.text()] = action.menu()
        return menus

    def _item_texts(self, menu):
        return [action.text() for action in menu.actions()
                if not action.isSeparator()]

    def test_menu_bar_has_file_relay_and_help(self):
        menus = self._menus()
        self.assertEqual(sorted(menus), ["File", "Help", "Relay"])
        self.assertEqual(self._item_texts(menus["File"]), [
            "Save conversation", "Open conversation",
            "Save full transcript", "Save both", "Debug report",
            "Quit"])
        self.assertEqual(self._item_texts(menus["Relay"]), [
            "Start relay", "Stop relay"])
        self.assertEqual(self._item_texts(menus["Help"]), [
            "Check for updates", "How to use", "About"])

    def test_shortcuts_are_set(self):
        actions = self.state["menu_actions"]
        self.assertEqual(
            actions["save_conversation"].shortcut().toString(), "Ctrl+S")
        self.assertEqual(
            actions["open_conversation"].shortcut().toString(), "Ctrl+O")
        self.assertEqual(
            actions["save_full"].shortcut().toString(), "Ctrl+Shift+S")
        self.assertEqual(actions["quit"].shortcut().toString(), "Ctrl+Q")

    def test_save_conversation_from_menu_writes_the_file(self):
        self.assertTrue(self._send_both_and_wait("hello both"))
        path = os.path.join(self.tmp.name, "convo.json")
        with self._save_dialog(path):
            self.state["menu_actions"]["save_conversation"].trigger()
        self.app.processEvents()
        new_convo, _new_log = load_conversation_file(path)
        self.assertEqual(len(new_convo.sides["A"].messages), 2)
        self.assertEqual(len(new_convo.sides["B"].messages), 2)

    def test_open_conversation_from_menu_restores_the_panes(self):
        self.assertTrue(self._send_both_and_wait("hello both"))
        path = os.path.join(self.tmp.name, "convo.json")
        with self._save_dialog(path):
            self.state["menu_actions"]["save_conversation"].trigger()
        self.state["widgets"]["A"]["clear"].click()
        self.state["widgets"]["B"]["clear"].click()
        self.app.processEvents()
        self.assertEqual(self.state["convo"].sides["A"].messages, [])
        with self._open_dialog(path):
            self.state["menu_actions"]["open_conversation"].trigger()
        self.app.processEvents()
        self.assertEqual(len(self.state["convo"].sides["A"].messages), 2)
        self.assertIn("hello both", self._transcript("A"))

    def test_save_full_transcript_from_menu(self):
        self.assertTrue(self._send_both_and_wait("hello both"))
        path = os.path.join(self.tmp.name, "full.txt")
        with self._save_dialog(path):
            self.state["menu_actions"]["save_full"].trigger()
        self.assertIn("hello both", self._read(path))

    def test_save_both_and_debug_report_from_menu(self):
        self.assertTrue(self._send_both_and_wait("hello both"))
        path = os.path.join(self.tmp.name, "both.txt")
        with self._save_dialog(path):
            self.state["menu_actions"]["save_both"].trigger()
        self.assertIn("hello both", self._read(path))
        report = os.path.join(self.tmp.name, "debug.txt")
        with self._save_dialog(report):
            self.state["menu_actions"]["debug_report"].trigger()
        text = self._read(report)
        self.assertIn("DUO", text)
        self.assertTrue(len(text) > 0)

    def test_relay_menu_starts_and_stops_a_relay(self):
        self.state["cap_spin"].setValue(50)
        self.state["composer"].setPlainText("relay seed")
        self.app.processEvents()
        self.state["menu_actions"]["start_relay"].trigger()
        self.assertIsNotNone(self.state["state"].get("relay"))
        self.state["menu_actions"]["stop_relay"].trigger()
        self.assertTrue(self._wait_for(
            lambda: self.state["state"].get("relay") is None))
        self.assertEqual(self.state["relay_status"].text(),
                         "Relay stopped: stopped by you.")

    def test_menu_states_mirror_buttons_when_busy(self):
        actions = self.state["menu_actions"]
        state = self.state["state"]
        self.assertTrue(actions["save_conversation"].isEnabled())
        self.assertTrue(self.state["save_convo"].isEnabled())
        self.assertTrue(actions["start_relay"].isEnabled())
        state["busy"] = True
        self.state["refresh_buttons"]()
        for name in ("save_conversation", "open_conversation",
                     "save_full", "save_both", "start_relay"):
            self.assertFalse(actions[name].isEnabled(), name)
        self.assertFalse(self.state["save_convo"].isEnabled())
        self.assertFalse(self.state["open_convo"].isEnabled())
        state["busy"] = False
        self.state["refresh_buttons"]()
        for name in ("save_conversation", "open_conversation",
                     "save_full", "save_both", "start_relay"):
            self.assertTrue(actions[name].isEnabled(), name)

    def test_relay_menu_states_follow_the_relay(self):
        actions = self.state["menu_actions"]
        state = self.state["state"]
        self.assertTrue(actions["start_relay"].isEnabled())
        self.assertFalse(actions["stop_relay"].isEnabled())
        state["relay"] = RelaySession("A")
        self.state["refresh_buttons"]()
        self.assertFalse(actions["start_relay"].isEnabled())
        self.assertTrue(actions["stop_relay"].isEnabled())
        self.assertEqual(self.state["relay_btn"].text(), "Stop Relay")
        state["relay"] = None
        self.state["refresh_buttons"]()
        self.assertTrue(actions["start_relay"].isEnabled())
        self.assertFalse(actions["stop_relay"].isEnabled())

    def test_about_dialog_shows_version_and_license(self):
        import duo_menu
        from PySide6 import QtWidgets
        dialog = duo_menu.make_about_dialog(self.window, APP_VERSION)
        try:
            labels = dialog.findChildren(QtWidgets.QLabel)
            text = "\n".join(label.text() for label in labels)
            self.assertIn("DUO %s" % APP_VERSION, text)
            self.assertIn("MIT", text)
            self.assertNotIn("\u2014", text)
            buttons = [btn for btn in
                       dialog.findChildren(QtWidgets.QPushButton)
                       if btn.text() == "Close"]
            self.assertEqual(len(buttons), 1)
            dialog.show()
            self.app.processEvents()
            self.assertTrue(dialog.isVisible())
            buttons[0].click()
            self.app.processEvents()
            self.assertFalse(dialog.isVisible())
        finally:
            dialog.close()

    def test_about_menu_item_opens_the_about_dialog(self):
        with mock.patch("duo_menu.show_about_dialog") as show:
            self.state["menu_actions"]["about"].trigger()
        show.assert_called_once_with(self.window, APP_VERSION)

    def test_how_to_use_dialog_shows_all_sections_and_closes(self):
        import duo_menu
        from PySide6 import QtWidgets
        dialog = duo_menu.make_how_to_use_dialog(self.window)
        try:
            edits = dialog.findChildren(QtWidgets.QTextEdit)
            self.assertEqual(len(edits), 1)
            text = edits[0].toPlainText()
            for heading, _body in duo_menu.HOW_TO_USE_SECTIONS:
                self.assertIn(heading, text)
            self.assertNotIn("\u2014", text)
            buttons = [btn for btn in
                       dialog.findChildren(QtWidgets.QPushButton)
                       if btn.text() == "Close"]
            self.assertEqual(len(buttons), 1)
            dialog.show()
            self.app.processEvents()
            self.assertTrue(dialog.isVisible())
            buttons[0].click()
            self.app.processEvents()
            self.assertFalse(dialog.isVisible())
        finally:
            dialog.close()

    def test_how_to_use_menu_item_opens_the_index(self):
        with mock.patch("duo_menu.show_how_to_use_dialog") as show:
            self.state["menu_actions"]["how_to_use"].trigger()
        show.assert_called_once_with(self.window)

    def test_check_for_updates_menu_item_reports_latest(self):
        self.state["menu_actions"]["check_updates"].trigger()
        self.assertTrue(self._wait_for(
            lambda: self.window.statusBar().currentMessage()
            == MSG_UPDATE_LATEST, timeout=10))

    def test_quit_menu_item_closes_the_window(self):
        self.state["menu_actions"]["quit"].trigger()
        self.assertTrue(self._wait_for(
            lambda: not self.window.isVisible(), timeout=10))

    def test_temperature_tooltip_and_nudge_have_no_em_dash(self):
        tip = self.state["widgets"]["A"]["temperature"].toolTip()
        self.assertIn(
            "0.7 is Ollama's default. Lower is more focused, "
            "higher is more random.", tip)
        self.assertNotIn("\u2014", tip)
        nudge = self.state["widgets"]["A"]["nudge"].text()
        self.assertEqual(nudge, "History is long. Summarize?")
@unittest.skipUnless(os.environ.get("DUAL_CHAT_GUI_TESTS") == "1",
                     "GUI tests run with DUAL_CHAT_GUI_TESTS=1")
class DirectorCollapseGuiTests(unittest.TestCase):
    """v0.31 (audit T19): one Director toggle collapses the whole
    director area below the panes so the panes take the full
    window. The toggle stays visible either way, the choice is
    remembered between runs, and collapsing never touches running
    work. The update check is stubbed, as in MenuBarGuiTests, so
    no test touches the network."""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        cls.client = FakeGuiClient()

    def setUp(self):
        # The collapsed choice is persisted, so clear it before
        # every window: each test starts from the default
        # (expanded).
        self._clear_director_collapsed()
        from dual_chat import build_window
        self._patcher = mock.patch(
            "dual_chat.check_for_update",
            return_value=("latest", None))
        self._patcher.start()
        self.client.reset()
        self.window = build_window(self.client, logger=TEST_LOGGER)
        self.window.show()
        self.state = self.window._dual_chat
        self._wait_for(lambda: self.state["widgets"]["A"]["picker"].count() == 2)
        self._pick_models()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self._patcher.stop()
        self._clear_director_collapsed()

    def _clear_director_collapsed(self):
        from PySide6.QtCore import QSettings
        settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        settings.remove(SETTING_DIRECTOR_COLLAPSED)
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

    def _start_relay(self, seed, cap=10):
        self.state["start_picker"].setCurrentIndex(0)
        self.state["cap_spin"].setValue(cap)
        self.state["mode_picker"].setCurrentIndex(0)
        self.state["stop_edit"].setText("")
        self.state["agree_check"].setChecked(False)
        self.state["composer"].setPlainText(seed)
        self.app.processEvents()
        self.state["relay_btn"].click()

    def _wait_for_relay_stopped(self, timeout=15.0):
        return self._wait_for(lambda: self.state["relay_status"].text()
                              .startswith("Relay stopped"), timeout=timeout)

    def test_director_starts_expanded(self):
        toggle = self.state["director_toggle"]
        self.assertEqual(toggle.text(), "Director \u25be")
        self.assertTrue(self.state["director"].isVisible())
        self.assertTrue(toggle.isVisible())
        self.assertTrue(self.state["composer"].isVisible())

    def test_collapse_hides_director_and_panes_grow(self):
        pane = self.state["widgets"]["A"]["transcript"]
        before = pane.height()
        self.state["director_toggle"].click()
        self.app.processEvents()
        self.assertTrue(self.state["director"].isHidden())
        self.assertEqual(self.state["director_toggle"].text(),
                         "Director \u25b8")
        self.assertTrue(self.state["director_toggle"].isVisible())
        self.assertTrue(self._wait_for(
            lambda: pane.height() > before, timeout=5),
            "the panes should grow into the freed space")

    def test_expand_restores_director(self):
        toggle = self.state["director_toggle"]
        toggle.click()
        self.app.processEvents()
        self.assertTrue(self.state["director"].isHidden())
        toggle.click()
        self.app.processEvents()
        self.assertTrue(self.state["director"].isVisible())
        self.assertEqual(toggle.text(), "Director \u25be")
        self.assertTrue(self.state["composer"].isVisible())
        self.assertTrue(self.state["relay_btn"].isVisible())

    def test_collapsed_state_persists_across_restart(self):
        from dual_chat import build_window
        self.state["director_toggle"].click()
        self.app.processEvents()
        self.assertTrue(self.state["director"].isHidden())
        self.window.close()
        self.app.processEvents()
        window2 = build_window(self.client, logger=TEST_LOGGER)
        try:
            window2.show()
            state2 = window2._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state2["widgets"]["A"]["picker"].count() == 2))
            self.assertTrue(state2["director"].isHidden())
            self.assertEqual(state2["director_toggle"].text(),
                             "Director \u25b8")
            self.assertTrue(state2["director_toggle"].isVisible())
            # And expanding persists the other way.
            state2["director_toggle"].click()
            self.app.processEvents()
            self.assertTrue(state2["director"].isVisible())
        finally:
            window2.close()
        self.app.processEvents()
        window3 = build_window(self.client, logger=TEST_LOGGER)
        try:
            window3.show()
            state3 = window3._dual_chat
            self.assertTrue(self._wait_for(
                lambda: state3["widgets"]["A"]["picker"].count() == 2))
            self.assertTrue(state3["director"].isVisible())
        finally:
            window3.close()

    def test_send_works_after_collapse_expand_cycle(self):
        self.state["director_toggle"].click()
        self.app.processEvents()
        self.state["director_toggle"].click()
        self.app.processEvents()
        self.state["composer"].setPlainText("Hello, both of you.")
        self.state["send_both"].click()
        ok = self._wait_for(lambda: (
            len(self.state["convo"].sides["A"].messages) >= 2
            and len(self.state["convo"].sides["B"].messages) >= 2
            and not self.state["state"]["busy"]), timeout=15)
        self.assertTrue(ok, "both panes should show their replies")

    def test_relay_keeps_running_while_collapsed(self):
        self._start_relay("Talk about rain.", cap=4)
        self.state["director_toggle"].click()
        self.app.processEvents()
        self.assertTrue(self.state["director"].isHidden())
        self.assertTrue(self._wait_for_relay_stopped(),
                        "the relay should run to its stop, collapsed "
                        "or not")
        self.assertGreaterEqual(
            len(self.state["convo"].sides["A"].messages), 2)
        self.assertGreaterEqual(
            len(self.state["convo"].sides["B"].messages), 2)

    def test_toggle_always_visible(self):
        toggle = self.state["director_toggle"]
        self.assertTrue(toggle.isVisible())
        toggle.click()
        self.app.processEvents()
        self.assertTrue(toggle.isVisible())
        toggle.click()
        self.app.processEvents()
        self.assertTrue(toggle.isVisible())


if __name__ == "__main__":
    unittest.main()
