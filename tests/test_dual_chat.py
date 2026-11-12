"""Tests for Dual Chat Item 1. No real Ollama needed: a fake Ollama HTTP
server (and a fake client for the GUI tests) stands in for the real one.

Run from the project root:  python3 -m unittest discover -s tests -v
"""
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
    Cancelled, ChatSide, Conversation, NoModelSelected, OllamaClient,
    OllamaError, SerialRunner, parse_ollama_list_output, parse_tags_payload,
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
    """Stands in for OllamaClient in the GUI tests."""

    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def list_models(self, timeout=5):
        return ["fake-a:latest", "fake-b:latest"]

    def chat_stream(self, model, messages, on_chunk, cancel_event, timeout=300):
        with self.lock:
            self.calls.append(model)
        text = "Reply from %s." % model
        for word in text.split(" "):
            if cancel_event.is_set():
                raise Cancelled()
            on_chunk(word + " ")
            time.sleep(0.02)
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


if __name__ == "__main__":
    unittest.main()
