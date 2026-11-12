#!/usr/bin/env python3
"""Dual Chat (Item 1) - two Ollama models side by side in one desktop window.

Item 1 scope only:
  * One native Qt window, Model A on the left, Model B on the right.
  * A model picker above each pane (Ollama localhost, nothing selected
    by default). Send is disabled for a side until a model is picked there.
  * One composer at the bottom with Send to A / Send to B / Send to Both.
  * Each side keeps its own separate conversation history.
  * ONE generation at a time across the whole app (Send to Both runs A
    first, then B) so two models never thrash the PC's RAM at once.
  * Per-side Stop and Clear buttons. Plain-language errors, no tracebacks.

NOT in Item 1 (later items): auto-relay, reorder, edit-before-forward,
file upload, tasks, system prompts, export, provider APIs.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request

APP_NAME = "Dual Chat"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_URL_ENV = "DUAL_CHAT_OLLAMA_URL"

# Plain-language messages shown in the panes. No tracebacks in the UI.
MSG_OLLAMA_DOWN = "Ollama isn't running. Start Ollama and try again."
MSG_MODEL_FAILED = "That model couldn't be loaded. Pick another model and try again."
MSG_NO_MODEL = "Pick a model for {side} first."
MSG_STOPPED = "Stopped."
MSG_EMPTY = "Type a message first."


class OllamaError(Exception):
    """A plain-language Ollama failure. ``message`` is safe to show."""


class Cancelled(Exception):
    """Raised inside a generation when the user stops it."""


class NoModelSelected(Exception):
    """Raised when a send targets a side with no model picked."""

    def __init__(self, side):
        super().__init__(MSG_NO_MODEL.format(side=side))
        self.side = side


def parse_tags_payload(payload):
    """Parse an /api/tags JSON payload into a sorted list of model names."""
    names = []
    for entry in payload.get("models", []) or []:
        name = entry.get("name")
        if name:
            names.append(name)
    return sorted(names)


def parse_ollama_list_output(text):
    """Parse `ollama list` table output into a sorted list of model names.

    The NAME column is first; the header line and blank lines are skipped.
    """
    names = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("NAME"):
            continue
        names.append(line.split()[0])
    return sorted(names)


class OllamaClient:
    """Minimal Ollama client over localhost HTTP. Stdlib only."""

    def __init__(self, base_url=None):
        self.base_url = (base_url or os.environ.get(OLLAMA_URL_ENV)
                         or DEFAULT_OLLAMA_URL).rstrip("/")

    def list_models(self, timeout=5):
        """Return installed model names, or raise OllamaError."""
        try:
            req = urllib.request.Request(self.base_url + "/api/tags")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return parse_tags_payload(json.loads(resp.read().decode("utf-8")))
        except Exception:
            pass  # Fall through to the `ollama list` fallback.
        try:
            if shutil.which("ollama") is None:
                raise OllamaError(MSG_OLLAMA_DOWN)
            out = subprocess.run(
                ["ollama", "list"], capture_output=True, text=True, timeout=15)
            if out.returncode != 0:
                raise OllamaError(MSG_OLLAMA_DOWN)
            return parse_ollama_list_output(out.stdout)
        except OllamaError:
            raise
        except Exception:
            raise OllamaError(MSG_OLLAMA_DOWN)

    def chat_stream(self, model, messages, on_chunk, cancel_event, timeout=300):
        """Stream one chat turn. Calls on_chunk(text) per delta.

        Returns the full reply text. Raises Cancelled if cancel_event is
        set, OllamaError with a plain message on failure.
        """
        body = json.dumps(
            {"model": model, "messages": messages, "stream": True}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            except Exception:
                detail = ""
            if "not found" in detail.lower() or "model" in detail.lower():
                raise OllamaError(MSG_MODEL_FAILED)
            raise OllamaError(MSG_MODEL_FAILED)
        except Exception:
            raise OllamaError(MSG_OLLAMA_DOWN)
        full = []
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise Cancelled()
                line = resp.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8"))
                except ValueError:
                    continue
                if chunk.get("error"):
                    raise OllamaError(MSG_MODEL_FAILED)
                content = (chunk.get("message") or {}).get("content", "")
                if content:
                    full.append(content)
                    on_chunk(content)
                if chunk.get("done"):
                    break
        finally:
            resp.close()
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()
        return "".join(full)


class ChatSide:
    """One side's state: its picked model and its own history."""

    def __init__(self, name):
        self.name = name  # "A" or "B"
        self.model = None
        self.history = []  # [{"role": "user"/"assistant", "content": str}]

    def add_user(self, text):
        self.history.append({"role": "user", "content": text})

    def add_assistant(self, text):
        self.history.append({"role": "assistant", "content": text})

    def clear(self):
        self.history = []


class Conversation:
    """Both sides. Histories are fully independent per side."""

    def __init__(self):
        self.sides = {"A": ChatSide("A"), "B": ChatSide("B")}

    def targets_for(self, action):
        """Return the ordered sides a send targets, or raise NoModelSelected.

        action is "A", "B" or "Both". "Both" always orders A first, then B.
        """
        wanted = ["A", "B"] if action == "Both" else [action]
        for side in wanted:
            if self.sides[side].model is None:
                raise NoModelSelected("Model " + side)
        return [self.sides[s] for s in wanted]


class SerialRunner:
    """Runs generation jobs strictly one at a time, in submit order.

    Jobs are dicts with keys: id, fn. fn(emit_chunk, cancel_event) returns
    the full reply text. Callbacks (all optional):
      on_start(job), on_chunk(job, text), on_done(job, full_text),
      on_error(job, plain_message), on_cancelled(job), on_idle().
    """

    def __init__(self, on_start=None, on_chunk=None, on_done=None,
                 on_error=None, on_cancelled=None, on_idle=None):
        self._queue = queue.Queue()
        self._cancelled = set()
        self._cancel_lock = threading.Lock()
        self._current_id = None
        self._current_event = None
        self.on_start = on_start or (lambda job: None)
        self.on_chunk = on_chunk or (lambda job, text: None)
        self.on_done = on_done or (lambda job, text: None)
        self.on_error = on_error or (lambda job, msg: None)
        self.on_cancelled = on_cancelled or (lambda job: None)
        self.on_idle = on_idle or (lambda: None)
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def submit(self, job_id, fn):
        self._queue.put({"id": job_id, "fn": fn})

    def cancel(self, job_id):
        """Cancel a queued job, or stop the running job if it matches."""
        with self._cancel_lock:
            self._cancelled.add(job_id)
            if self._current_id == job_id and self._current_event is not None:
                self._current_event.set()

    def _loop(self):
        while True:
            job = self._queue.get()
            job_id = job["id"]
            with self._cancel_lock:
                was_cancelled = job_id in self._cancelled
                if was_cancelled:
                    self._cancelled.discard(job_id)
            if was_cancelled:
                self.on_cancelled(job)
            else:
                event = threading.Event()
                with self._cancel_lock:
                    self._current_id = job_id
                    self._current_event = event
                    if job_id in self._cancelled:
                        # Cancelled between dequeue and start.
                        self._cancelled.discard(job_id)
                        event.set()
                try:
                    self.on_start(job)
                    full = job["fn"](
                        lambda text: self.on_chunk(job, text), event)
                    self.on_done(job, full)
                except Cancelled:
                    self.on_cancelled(job)
                except OllamaError as exc:
                    self.on_error(job, str(exc))
                except Exception:
                    self.on_error(job, MSG_MODEL_FAILED)
                finally:
                    with self._cancel_lock:
                        self._current_id = None
                        self._current_event = None
            self._queue.task_done()
            if self._queue.empty():
                self.on_idle()


# ---------------------------------------------------------------------------
# Qt GUI
# ---------------------------------------------------------------------------

def _qt():
    from PySide6 import QtCore, QtWidgets  # noqa: F401  (imported on demand)
    return QtCore, QtWidgets


class _Signals:
    """QObject bridge: runner callbacks (worker thread) -> Qt signals."""

    def __init__(self):
        QtCore, _ = _qt()
        self._obj = _SignalObject()

    def __getattr__(self, name):
        return getattr(self._obj, name)


class _SignalObject:
    def __init__(self):
        from PySide6.QtCore import QObject, Signal

        class _Obj(QObject):
            started = Signal(str)
            chunk = Signal(str, str)
            done = Signal(str, str)
            failed = Signal(str, str)
            cancelled = Signal(str)
            idle = Signal()
            models_loaded = Signal(list)
            models_failed = Signal(str)

        self._inner = _Obj()
        self.started = self._inner.started
        self.chunk = self._inner.chunk
        self.done = self._inner.done
        self.failed = self._inner.failed
        self.cancelled = self._inner.cancelled
        self.idle = self._inner.idle
        self.models_loaded = self._inner.models_loaded
        self.models_failed = self._inner.models_failed


class _ModelLoader:
    """Loads the model list off the GUI thread."""

    def __init__(self, client, signals):
        from PySide6.QtCore import QThread

        class _Thread(QThread):
            def run(self):
                try:
                    models = client.list_models()
                    signals.models_loaded.emit(models)
                except OllamaError as exc:
                    signals.models_failed.emit(str(exc))

        self.thread = _Thread()


class MainWindow:
    """Built by build_window(); kept import-light so tests can skip Qt."""

    pass


def build_window(client=None):
    """Create the main window. Returns (window, app_state_dict)."""
    from PySide6 import QtCore, QtWidgets

    client = client or OllamaClient()
    signals = _Signals()
    convo = Conversation()

    window = QtWidgets.QMainWindow()
    window.setWindowTitle(APP_NAME)
    window.resize(1100, 680)  # Fits a 1366x768 screen with room to spare.
    window.setMinimumSize(900, 540)

    central = QtWidgets.QWidget()
    window.setCentralWidget(central)
    root = QtWidgets.QVBoxLayout(central)
    root.setContentsMargins(8, 8, 8, 8)
    root.setSpacing(8)

    panes = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
    root.addWidget(panes, stretch=1)

    # --- one pane per side -----------------------------------------------
    widgets = {}  # side -> dict of widgets

    def make_pane(side):
        frame = QtWidgets.QFrame()
        frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        title = QtWidgets.QLabel("Model " + side)
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        layout.addWidget(title)

        picker_row = QtWidgets.QHBoxLayout()
        picker = QtWidgets.QComboBox()
        picker.setPlaceholderText("Pick a model")
        picker.setSizeAdjustPolicy(
            QtWidgets.QComboBox.AdjustToMinimumContentsLengthWithIcon)
        picker.setMinimumContentsLength(20)
        picker_row.addWidget(picker, stretch=1)
        refresh = QtWidgets.QPushButton("Refresh")
        picker_row.addWidget(refresh)
        layout.addLayout(picker_row)

        transcript = QtWidgets.QTextEdit()
        transcript.setReadOnly(True)
        transcript.setPlaceholderText("No messages yet.")
        layout.addWidget(transcript, stretch=1)

        bottom = QtWidgets.QHBoxLayout()
        status = QtWidgets.QLabel("Idle")
        bottom.addWidget(status, stretch=1)
        stop = QtWidgets.QPushButton("Stop")
        stop.setEnabled(False)
        bottom.addWidget(stop)
        clear = QtWidgets.QPushButton("Clear")
        bottom.addWidget(clear)
        layout.addLayout(bottom)

        widgets[side] = {
            "picker": picker, "refresh": refresh, "transcript": transcript,
            "status": status, "stop": stop, "clear": clear,
            "reply_open": False,
        }
        return frame

    panes.addWidget(make_pane("A"))
    panes.addWidget(make_pane("B"))
    panes.setSizes([550, 550])

    # --- director bar -----------------------------------------------------
    director = QtWidgets.QFrame()
    director.setFrameShape(QtWidgets.QFrame.StyledPanel)
    d_layout = QtWidgets.QVBoxLayout(director)
    d_layout.setContentsMargins(6, 6, 6, 6)
    d_layout.setSpacing(6)
    composer = QtWidgets.QPlainTextEdit()
    composer.setPlaceholderText("Type a message for the models...")
    composer.setMaximumBlockCount(5)
    composer.setFixedHeight(64)
    d_layout.addWidget(composer)
    btn_row = QtWidgets.QHBoxLayout()
    hint = QtWidgets.QLabel("Send to one side, or to both at once.")
    btn_row.addWidget(hint, stretch=1)
    send_a = QtWidgets.QPushButton("Send to A")
    send_b = QtWidgets.QPushButton("Send to B")
    send_both = QtWidgets.QPushButton("Send to Both")
    for btn in (send_a, send_b, send_both):
        btn.setEnabled(False)
        btn_row.addWidget(btn)
    d_layout.addLayout(btn_row)
    root.addWidget(director)

    statusbar = window.statusBar()
    statusbar.showMessage("Loading models...")

    state = {"busy": False, "active": set(), "queued": set()}

    # --- helpers ----------------------------------------------------------
    def scroll_to_end(transcript):
        bar = transcript.verticalScrollBar()
        bar.setValue(bar.maximum())

    def append_text(side, text):
        t = widgets[side]["transcript"]
        cursor = t.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        t.setTextCursor(cursor)
        scroll_to_end(t)

    def refresh_buttons():
        busy = state["busy"]
        a_ready = convo.sides["A"].model is not None
        b_ready = convo.sides["B"].model is not None
        send_a.setEnabled(not busy and a_ready)
        send_b.setEnabled(not busy and b_ready)
        send_both.setEnabled(not busy and a_ready and b_ready)
        for side in ("A", "B"):
            w = widgets[side]
            w["picker"].setEnabled(not busy)
            w["refresh"].setEnabled(not busy)
            w["stop"].setEnabled(side in state["active"]
                                 or side in state["queued"])
            w["clear"].setEnabled(side not in state["active"]
                                  and side not in state["queued"])

    def set_status(side, text):
        widgets[side]["status"].setText(text)

    # --- runner wiring ----------------------------------------------------
    def make_job_fn(side, model, messages):
        def fn(emit, cancel_event):
            return client.chat_stream(model, messages, emit, cancel_event)
        return fn

    runner = SerialRunner(
        on_start=lambda job: signals.started.emit(job["id"]),
        on_chunk=lambda job, text: signals.chunk.emit(job["id"], text),
        on_done=lambda job, text: signals.done.emit(job["id"], text),
        on_error=lambda job, msg: signals.failed.emit(job["id"], msg),
        on_cancelled=lambda job: signals.cancelled.emit(job["id"]),
        on_idle=lambda: signals.idle.emit(),
    )

    def on_started(side):
        state["queued"].discard(side)
        state["active"].add(side)
        set_status(side, "Thinking...")
        widgets[side]["reply_open"] = True
        append_text(side, "%s: " % convo.sides[side].model)
        refresh_buttons()

    def on_chunk(side, text):
        append_text(side, text)

    def finish_reply(side):
        if widgets[side]["reply_open"]:
            append_text(side, "\n")
            widgets[side]["reply_open"] = False

    def on_done(side, text):
        finish_reply(side)
        convo.sides[side].add_assistant(text)
        state["active"].discard(side)
        set_status(side, "Idle")
        refresh_buttons()

    def on_failed(side, message):
        finish_reply(side)
        append_text(side, "(%s)\n" % message)
        state["active"].discard(side)
        set_status(side, "Idle")
        refresh_buttons()

    def on_cancelled(side):
        finish_reply(side)
        append_text(side, "(%s)\n" % MSG_STOPPED)
        state["active"].discard(side)
        state["queued"].discard(side)
        set_status(side, "Idle")
        refresh_buttons()

    def on_idle():
        state["busy"] = False
        refresh_buttons()

    signals.started.connect(on_started)
    signals.chunk.connect(on_chunk)
    signals.done.connect(on_done)
    signals.failed.connect(on_failed)
    signals.cancelled.connect(on_cancelled)
    signals.idle.connect(on_idle)

    # --- sends ------------------------------------------------------------
    def do_send(action):
        text = composer.toPlainText().strip()
        if not text:
            statusbar.showMessage(MSG_EMPTY, 3000)
            return
        try:
            targets = convo.targets_for(action)
        except NoModelSelected as exc:
            statusbar.showMessage(str(exc), 4000)
            return
        state["busy"] = True
        composer.clear()
        for side_obj in targets:
            side = side_obj.name
            side_obj.add_user(text)
            append_text(side, "You: %s\n" % text)
            state["queued"].add(side)
            set_status(side, "Queued...")
            runner.submit(side, make_job_fn(
                side, side_obj.model, list(side_obj.history)))
        refresh_buttons()
        composer.setFocus()

    send_a.clicked.connect(lambda: do_send("A"))
    send_b.clicked.connect(lambda: do_send("B"))
    send_both.clicked.connect(lambda: do_send("Both"))

    # --- per-side buttons --------------------------------------------------
    def make_stop(side):
        def stop():
            runner.cancel(side)
        return stop

    def make_clear(side):
        def clear():
            convo.sides[side].clear()
            widgets[side]["transcript"].clear()
            set_status(side, "Idle")
        return clear

    for side in ("A", "B"):
        widgets[side]["stop"].clicked.connect(make_stop(side))
        widgets[side]["clear"].clicked.connect(make_clear(side))

    # --- model pickers -----------------------------------------------------
    def on_picker_changed(side):
        def changed(index):
            picker = widgets[side]["picker"]
            convo.sides[side].model = (
                picker.currentText() if index >= 0 else None)
            refresh_buttons()
        return changed

    for side in ("A", "B"):
        widgets[side]["picker"].currentIndexChanged.connect(
            on_picker_changed(side))

    def populate_models(models):
        for side in ("A", "B"):
            picker = widgets[side]["picker"]
            previous = convo.sides[side].model
            picker.blockSignals(True)
            picker.clear()
            picker.addItems(models)
            if previous in models:
                picker.setCurrentIndex(models.index(previous))
            else:
                # Nothing selected by default, ever.
                picker.setCurrentIndex(-1)
                convo.sides[side].model = None
            picker.blockSignals(False)
        if models:
            statusbar.showMessage(
                "Ollama connected: %d model(s) found." % len(models))
        else:
            statusbar.showMessage(
                "Ollama connected but no models found. Pull a model first.")
        refresh_buttons()

    def on_models_failed(message):
        statusbar.showMessage(message)
        refresh_buttons()

    signals.models_loaded.connect(populate_models)
    signals.models_failed.connect(on_models_failed)

    loaders = []

    def load_models():
        statusbar.showMessage("Loading models...")
        loader = _ModelLoader(client, signals)
        loaders.append(loader)
        loader.thread.finished.connect(
            lambda: loaders.remove(loader) if loader in loaders else None)
        loader.thread.start()

    for side in ("A", "B"):
        widgets[side]["refresh"].clicked.connect(load_models)

    window._dual_chat = {  # test hooks (not part of the UI)
        "convo": convo, "runner": runner, "signals": signals,
        "widgets": widgets, "composer": composer,
        "send_a": send_a, "send_b": send_b, "send_both": send_both,
        "do_send": do_send, "load_models": load_models, "state": state,
    }

    load_models()
    refresh_buttons()
    return window


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    base_url = None
    if "--ollama-url" in argv:
        i = argv.index("--ollama-url")
        if i + 1 < len(argv):
            base_url = argv[i + 1]
    from PySide6 import QtWidgets
    app = QtWidgets.QApplication(argv)
    window = build_window(OllamaClient(base_url))
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
