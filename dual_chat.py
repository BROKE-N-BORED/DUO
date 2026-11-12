#!/usr/bin/env python3
"""DUO (Item 3) - two Ollama models side by side in one desktop window.

Item 1: the shell, the model pickers, and send-to-A/B/both.
Item 2: the auto-relay AI-to-AI loop. The user types a seed message, picks
  the starting side, and presses Start Relay. Each reply is handed to the
  other model, one generation at a time, until the turn cap, the Stop
  button, or a stop condition. Blind mode (default) delivers the bare
  reply text as a plain user message, so each model sees only a person
  talking to it. Aware mode frames the relay so the models know they are
  talking to another AI. Relayed hops are tagged in the window's
  transcripts only -- the models never see the tag.
Item 3: director controls. Reorder or delete any message in a side's
  history (the model sees the edited order on the next turn); review
  each relay hop before it is forwarded, editing it if you like;
  per-side instructions (system prompts) plus saved model pairs; and
  compare mode -- after Send to Both, pick the better reply and the
  conversation continues from it.
Item 4: long-loop support. Each pane gets a Summarize button: that
  side's own model summarizes the conversation so far, and the older
  turns are replaced by that summary (kept as a visible "Summary of
  earlier messages" entry) while the last 4 entries stay verbatim.
  A budget nudge appears when a side's history passes ~12,000
  characters -- it never calls the model on its own. And each pane
  shows a stats line for the last completed turn: tokens/sec, time
  to first token, total turn time, and the model's RAM from
  Ollama /api/ps.

NOT in Item 4 (later items): file upload, tasks, export, provider APIs.
"""

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

APP_NAME = "DUO"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_URL_ENV = "DUAL_CHAT_OLLAMA_URL"

# Plain-language messages shown in the panes. No tracebacks in the UI.
MSG_OLLAMA_DOWN = "Ollama isn't running. Start Ollama and try again."
MSG_MODEL_FAILED = "That model couldn't be loaded. Pick another model and try again."
MSG_NO_MODEL = "Pick a model for {side} first."
MSG_STOPPED = "Stopped."
MSG_EMPTY = "Type a message first."
MSG_RELAY_NEEDS_BOTH = "Pick a model for both sides to start a relay."
MSG_RELAY_STOPPED_BY_YOU = "stopped by you"

# Agreement stop: simple case-insensitive substring matching on this
# fixed list. No LLM judge -- the 2B models are slow enough already.
AGREEMENT_PHRASES = ("i agree", "agreed", "sounds good", "that works")
AWARE_TEMPLATE = ("You are talking with another AI assistant (Model %s). "
                  "It said: %s")
RELAY_CAP_MIN = 1
RELAY_CAP_MAX = 50
RELAY_CAP_DEFAULT = 10

# Summarize (Item 4): when a side's history passes this many characters
# the pane nudges the user to summarize. Summarizing keeps the last
# SUMMARY_KEEP_LAST entries verbatim and replaces everything older
# with one summary entry, written by that side's own model.
HISTORY_BUDGET_CHARS = 12000
SUMMARY_KEEP_LAST = 4
SUMMARY_LABEL = "Summary of earlier messages"
SUMMARIZE_PROMPT = (
    "Summarize our conversation so far in a few short sentences, in "
    "the third person, covering the main topics and any decisions. "
    "Keep it brief -- it will replace the earlier messages as context.")

# Compare mode: the picked reply is sent to the other side as a user
# message with this prefix, so the conversation continues from it.
COMPARE_PREFIX = "You picked this answer: %s"

MSG_PAIR_NEEDS_NAME = "Name the pair first."
MSG_REVIEW_EMPTY = "Nothing to forward -- edit the text or skip this hop."

# Saved model pairs live in a plain JSON file next to the app.
# DUAL_CHAT_PAIRS_FILE points at a different file (used by the tests).
PAIRS_ENV = "DUAL_CHAT_PAIRS_FILE"
PAIRS_FILENAME = "duo-pairs.json"


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


def parse_ps_payload(payload):
    """Parse an /api/ps JSON payload into {model name: size in bytes}."""
    running = {}
    for entry in payload.get("models", []) or []:
        name = entry.get("name")
        size = entry.get("size")
        if name and isinstance(size, (int, float)):
            running[name] = size
    return running


def compute_turn_stats(metrics):
    """Turn metrics into display numbers.

    metrics comes from the streamed done chunk plus client-side
    timing: eval_count, eval_duration_ns, ttft_s, total_s. Anything
    missing (or not a number) stays None and shows as "n/a" -- never
    invented. tokens/sec is eval_count over eval_duration in seconds.
    """
    count = metrics.get("eval_count")
    duration = metrics.get("eval_duration_ns")
    tok_per_sec = None
    if (isinstance(count, (int, float))
            and isinstance(duration, (int, float)) and duration > 0):
        tok_per_sec = count / (duration / 1_000_000_000)

    def _num(value):
        return value if isinstance(value, (int, float)) else None

    return {"tok_per_sec": tok_per_sec,
            "ttft_s": _num(metrics.get("ttft_s")),
            "total_s": _num(metrics.get("total_s"))}


def format_ram(size_bytes):
    """A model size in bytes as "1.9 GB" or "512 MB"."""
    if size_bytes >= 1_000_000_000:
        return "%.1f GB" % (size_bytes / 1_000_000_000)
    return "%d MB" % round(size_bytes / 1_000_000)


STATS_EMPTY_LINE = "-- tok/s · first token -- · turn -- · RAM --"


def format_stats_line(stats):
    """One line for a pane's stats label.

    stats is {"tok_per_sec": float|None, "ttft_s": float|None,
    "total_s": float|None, "ram": str|None} -- "ram" is preformatted,
    e.g. "1.9 GB in RAM" or "not loaded". None (before any turn) shows
    dashes; a missing number shows "n/a".
    """
    if stats is None:
        return STATS_EMPTY_LINE
    tok = stats.get("tok_per_sec")
    ttft = stats.get("ttft_s")
    total = stats.get("total_s")
    tok_text = ("%.1f tok/s" % tok) if tok is not None else "tok/s n/a"
    ttft_text = (("first token %.2fs" % ttft) if ttft is not None
                 else "first token n/a")
    total_text = (("turn %.1fs" % total) if total is not None
                  else "turn n/a")
    ram_text = stats.get("ram") or "RAM n/a"
    return "%s · %s · %s · %s" % (tok_text, ttft_text, total_text, ram_text)


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

    def chat_stream(self, model, messages, on_chunk, cancel_event,
                    timeout=300, on_metrics=None):
        """Stream one chat turn. Calls on_chunk(text) per delta.

        Returns the full reply text. Raises Cancelled if cancel_event is
        set, OllamaError with a plain message on failure.

        on_metrics (optional) is called once at the end with the turn's
        timing: eval_count and eval_duration_ns from the streamed done
        chunk, plus client-side ttft_s (request start to first content
        chunk) and total_s. Missing fields are None.
        """
        body = json.dumps(
            {"model": model, "messages": messages, "stream": True}).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        started = time.monotonic()
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
        ttft = None
        final = {}
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
                    if ttft is None:
                        ttft = time.monotonic() - started
                    full.append(content)
                    on_chunk(content)
                if chunk.get("done"):
                    final = chunk
                    break
        finally:
            resp.close()
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()
        if on_metrics is not None:
            on_metrics({
                "eval_count": final.get("eval_count"),
                "eval_duration_ns": final.get("eval_duration"),
                "total_duration_ns": final.get("total_duration"),
                "prompt_eval_duration_ns": final.get("prompt_eval_duration"),
                "ttft_s": ttft,
                "total_s": time.monotonic() - started,
            })
        return "".join(full)

    def running_models(self, timeout=5):
        """Models Ollama currently has loaded: {name: size in bytes}."""
        try:
            req = urllib.request.Request(self.base_url + "/api/ps")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return parse_ps_payload(json.loads(resp.read().decode("utf-8")))
        except Exception:
            raise OllamaError(MSG_OLLAMA_DOWN)


class ChatSide:
    """One side's state: its picked model and its own history.

    ``labels`` runs parallel to ``history``: the display label for each
    entry ("You", "You [relay from Model A]", or the model name). Labels
    are window-only -- they never reach the model. ``instruction`` is
    this side's system prompt ("" means no system message).
    """

    def __init__(self, name):
        self.name = name  # "A" or "B"
        self.model = None
        self.history = []  # [{"role": "user"/"assistant", "content": str}]
        self.labels = []
        self.instruction = ""

    def add_user(self, text, label="You"):
        self.history.append({"role": "user", "content": text})
        self.labels.append(label)

    def add_assistant(self, text, label=None):
        self.history.append({"role": "assistant", "content": text})
        self.labels.append(
            label if label is not None else (self.model or "Assistant"))

    def clear(self):
        self.history = []
        self.labels = []

    def history_chars(self):
        """Total characters across this side's history contents."""
        return sum(len(m["content"]) for m in self.history)

    def summarize(self, summary_text):
        """Fold the older turns into one summary entry.

        Keeps the last SUMMARY_KEEP_LAST entries verbatim; everything
        older is replaced by a single user entry carrying the summary,
        labelled SUMMARY_LABEL in the window. Returns False (and
        changes nothing) when there is nothing older to fold or the
        summary is empty.
        """
        if not summary_text.strip():
            return False
        if len(self.history) <= SUMMARY_KEEP_LAST:
            return False
        kept = self.history[-SUMMARY_KEEP_LAST:]
        kept_labels = self.labels[-SUMMARY_KEEP_LAST:]
        entry = {"role": "user",
                 "content": "Earlier in this conversation: "
                            + summary_text.strip()}
        self.history = [entry] + kept
        self.labels = [SUMMARY_LABEL] + kept_labels
        return True

    def move_up(self, index):
        """Move entry ``index`` one slot earlier. No-op at the edges."""
        if 0 < index < len(self.history):
            self._swap(index, index - 1)

    def move_down(self, index):
        """Move entry ``index`` one slot later. No-op at the edges."""
        if 0 <= index < len(self.history) - 1:
            self._swap(index, index + 1)

    def _swap(self, i, j):
        self.history[i], self.history[j] = self.history[j], self.history[i]
        self.labels[i], self.labels[j] = self.labels[j], self.labels[i]

    def delete_at(self, index):
        """Remove entry ``index``. No-op when out of range."""
        if 0 <= index < len(self.history):
            del self.history[index]
            del self.labels[index]

    def messages_for_model(self):
        """The messages a generation sends: system prompt (when set)
        followed by this side's history, in its current order."""
        messages = list(self.history)
        if self.instruction.strip():
            return [{"role": "system", "content": self.instruction}] + messages
        return messages


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


def parse_stop_phrases(text):
    """Split a comma-separated stop-phrase field into clean phrases."""
    return [p.strip() for p in (text or "").split(",") if p.strip()]


def pairs_path():
    """Where saved pairs live: duo-pairs.json next to the app.

    DUAL_CHAT_PAIRS_FILE overrides it (the tests point it at a temp file).
    """
    override = os.environ.get(PAIRS_ENV)
    if override:
        return override
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        PAIRS_FILENAME)


def load_pairs(path):
    """Saved pairs as {name: setup} from a JSON file.

    Missing, unreadable, or malformed files read as no pairs -- a bad
    file never stops the app from starting.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    pairs = data.get("pairs") if isinstance(data, dict) else None
    if not isinstance(pairs, dict):
        return {}
    return {name: setup for name, setup in pairs.items()
            if isinstance(setup, dict)}


def save_pair(path, name, setup):
    """Save one pair (creating or overwriting it) in the JSON file.

    A setup is {"model_a", "model_b", "instruction_a",
    "instruction_b"}; models may be None when nothing is picked.
    """
    pairs = load_pairs(path)
    pairs[name] = setup
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"pairs": pairs}, fh, indent=2, sort_keys=True)
        fh.write("\n")


class RelaySession:
    """One relay run: config, turn counting, framing, stop checks.

    Pure logic, no Qt -- the GUI drives it from the runner callbacks.
    A "turn" is one generation. The seed turn is turn 1, so a cap of N
    means exactly N generations before the relay halts.
    """

    def __init__(self, start_side, turn_cap=RELAY_CAP_DEFAULT, mode="blind",
                 stop_phrases=(), stop_on_agreement=False):
        self.start_side = start_side
        self.current_side = start_side  # side whose turn is in flight
        self.hop_from = "you"  # who the current turn is answering
        self.turn_cap = max(RELAY_CAP_MIN,
                            min(RELAY_CAP_MAX, int(turn_cap)))
        self.mode = mode  # "blind" or "aware"
        self.stop_phrases = list(stop_phrases)
        self.stop_on_agreement = stop_on_agreement
        self.turns_done = 0
        self.active = True
        self.stop_reason = None

    @staticmethod
    def other(side):
        return "B" if side == "A" else "A"

    def frame(self, text, from_side):
        """The message the other model receives for a relayed reply.

        Blind: the bare reply text, no framing at all -- the model sees
        only a person talking to it. Aware: framed so the models know
        they are talking to another AI.
        """
        if self.mode == "aware":
            return AWARE_TEMPLATE % (from_side, text)
        return text

    def find_stop_phrase(self, text):
        """The configured stop phrase found in text, or None.

        Case-insensitive substring match.
        """
        lowered = text.lower()
        for phrase in self.stop_phrases:
            if phrase.lower() in lowered:
                return phrase
        return None

    def find_agreement(self, text):
        """The agreement phrase found in text, or None.

        Only when stop-on-agreement is enabled. Case-insensitive
        substring match on the fixed AGREEMENT_PHRASES list.
        """
        if not self.stop_on_agreement:
            return None
        lowered = text.lower()
        for phrase in AGREEMENT_PHRASES:
            if phrase in lowered:
                return phrase
        return None

    def check_stop(self, reply_text):
        """Stop reason for this reply, or None to keep relaying.

        Call after incrementing turns_done. A phrase or agreement stop
        is reported before the cap when they land on the same turn,
        because it says more about why the relay ended.
        """
        phrase = self.find_stop_phrase(reply_text)
        if phrase is not None:
            return "phrase %r found" % phrase
        agreement = self.find_agreement(reply_text)
        if agreement is not None:
            return "agreement reached (%r)" % agreement
        if self.turns_done >= self.turn_cap:
            return "turn cap reached"
        return None

    def status_running(self):
        """Live status line, e.g. "Relay on: turn 3/10, A to B"."""
        return "Relay on: turn %d/%d, %s to %s" % (
            self.turns_done + 1, self.turn_cap,
            self.hop_from, self.current_side)


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
                    try:
                        signals.models_loaded.emit(models)
                    except RuntimeError:
                        pass  # Window gone; nothing left to update.
                except OllamaError as exc:
                    try:
                        signals.models_failed.emit(str(exc))
                    except RuntimeError:
                        pass  # Window gone; nothing left to update.

        self.thread = _Thread()


class MainWindow:
    """Built by build_window(); kept import-light so tests can skip Qt."""

    pass


def build_window(client=None):
    """Create the main window. Returns (window, app_state_dict)."""
    from PySide6 import QtCore, QtWidgets
    import shiboken6

    client = client or OllamaClient()
    signals = _Signals()
    convo = Conversation()

    window = QtWidgets.QMainWindow()
    window.setWindowTitle(APP_NAME)
    window.resize(1100, 680)  # Fits a 1366x768 screen with room to spare.
    window.setMinimumSize(900, 540)

    def alive():
        # Queued runner signals can land after the window is gone (e.g.
        # closing right as a relay stops). Never touch widgets then.
        return shiboken6.isValid(window)

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

        title_row = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Model " + side)
        title.setStyleSheet("font-weight: bold; font-size: 14px;")
        title_row.addWidget(title, stretch=1)
        instr_toggle = QtWidgets.QPushButton("Instructions ▸")
        instr_toggle.setCheckable(True)
        instr_toggle.setFlat(True)
        title_row.addWidget(instr_toggle)
        layout.addLayout(title_row)

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

        # Per-side instructions (system prompt). Collapsed by default;
        # empty means no system message is sent for this side.
        instr_edit = QtWidgets.QPlainTextEdit()
        instr_edit.setPlaceholderText(
            "Instructions for this model (system prompt)...")
        instr_edit.setFixedHeight(44)
        instr_edit.setVisible(False)
        layout.addWidget(instr_edit)

        transcript = QtWidgets.QTextEdit()
        transcript.setReadOnly(True)
        transcript.setPlaceholderText("No messages yet.")
        layout.addWidget(transcript, stretch=1)

        # Stats line: the last completed turn's speed and RAM. Dashes
        # until a turn has run.
        stats = QtWidgets.QLabel(format_stats_line(None))
        stats.setStyleSheet("font-size: 11px;")
        layout.addWidget(stats)

        # Summarize row: the nudge label appears when this side's
        # history passes the budget; the button is always here.
        summarize_row = QtWidgets.QHBoxLayout()
        nudge = QtWidgets.QLabel("History is long — Summarize?")
        nudge.setVisible(False)
        summarize_row.addWidget(nudge)
        summarize_btn = QtWidgets.QPushButton("Summarize")
        summarize_btn.setEnabled(False)
        summarize_row.addWidget(summarize_btn)
        summarize_row.addStretch(1)
        layout.addLayout(summarize_row)

        # History editor: reorder or delete any message. Hidden by
        # default; the model sees the edited order on the next turn.
        hist_frame = QtWidgets.QFrame()
        hist_layout = QtWidgets.QVBoxLayout(hist_frame)
        hist_layout.setContentsMargins(0, 0, 0, 0)
        hist_layout.setSpacing(4)
        hist_list = QtWidgets.QListWidget()
        hist_list.setFixedHeight(110)
        hist_layout.addWidget(hist_list)
        hist_btn_row = QtWidgets.QHBoxLayout()
        hist_up = QtWidgets.QPushButton("Up")
        hist_down = QtWidgets.QPushButton("Down")
        hist_delete = QtWidgets.QPushButton("Delete")
        for btn in (hist_up, hist_down, hist_delete):
            btn.setEnabled(False)
            hist_btn_row.addWidget(btn)
        hist_btn_row.addStretch(1)
        hist_layout.addLayout(hist_btn_row)
        hist_frame.setVisible(False)
        layout.addWidget(hist_frame)

        bottom = QtWidgets.QHBoxLayout()
        status = QtWidgets.QLabel("Idle")
        bottom.addWidget(status, stretch=1)
        use_this = QtWidgets.QPushButton("Use this")
        use_this.setEnabled(False)
        bottom.addWidget(use_this)
        stop = QtWidgets.QPushButton("Stop")
        stop.setEnabled(False)
        bottom.addWidget(stop)
        clear = QtWidgets.QPushButton("Clear")
        bottom.addWidget(clear)
        hist_toggle = QtWidgets.QPushButton("History ▸")
        hist_toggle.setCheckable(True)
        hist_toggle.setFlat(True)
        bottom.addWidget(hist_toggle)
        layout.addLayout(bottom)

        widgets[side] = {
            "picker": picker, "refresh": refresh, "transcript": transcript,
            "status": status, "stop": stop, "clear": clear,
            "instr_toggle": instr_toggle, "instr_edit": instr_edit,
            "use_this": use_this, "hist_toggle": hist_toggle,
            "hist_frame": hist_frame, "hist_list": hist_list,
            "hist_up": hist_up, "hist_down": hist_down,
            "hist_delete": hist_delete,
            "stats": stats, "nudge": nudge, "summarize": summarize_btn,
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

    # --- relay row ---------------------------------------------------------
    relay_row = QtWidgets.QHBoxLayout()
    relay_btn = QtWidgets.QPushButton("Start Relay")
    relay_btn.setEnabled(False)
    relay_row.addWidget(relay_btn)
    relay_row.addWidget(QtWidgets.QLabel("Start with:"))
    start_picker = QtWidgets.QComboBox()
    start_picker.addItems(["A", "B"])
    relay_row.addWidget(start_picker)
    relay_row.addWidget(QtWidgets.QLabel("Turns:"))
    cap_spin = QtWidgets.QSpinBox()
    cap_spin.setRange(RELAY_CAP_MIN, RELAY_CAP_MAX)
    cap_spin.setValue(RELAY_CAP_DEFAULT)
    relay_row.addWidget(cap_spin)
    mode_picker = QtWidgets.QComboBox()
    mode_picker.addItems(["Blind", "Aware"])
    relay_row.addWidget(mode_picker)
    relay_row.addWidget(QtWidgets.QLabel("Stop on:"))
    stop_edit = QtWidgets.QLineEdit()
    stop_edit.setPlaceholderText("words, separated by commas")
    relay_row.addWidget(stop_edit, stretch=1)
    agree_check = QtWidgets.QCheckBox("Stop on agreement")
    relay_row.addWidget(agree_check)
    review_check = QtWidgets.QCheckBox("Review each hop")
    relay_row.addWidget(review_check)
    d_layout.addLayout(relay_row)

    # --- saved pairs -------------------------------------------------------
    pair_row = QtWidgets.QHBoxLayout()
    pair_row.addWidget(QtWidgets.QLabel("Pair:"))
    pair_combo = QtWidgets.QComboBox()
    pair_combo.setPlaceholderText("Saved pairs")
    pair_row.addWidget(pair_combo, stretch=1)
    pair_name = QtWidgets.QLineEdit()
    pair_name.setPlaceholderText("Name for this pair")
    pair_row.addWidget(pair_name, stretch=1)
    pair_save = QtWidgets.QPushButton("Save pair")
    pair_row.addWidget(pair_save)
    d_layout.addLayout(pair_row)

    # --- hop review (shown only while a relay waits on a review) ----------
    review_frame = QtWidgets.QFrame()
    review_frame.setFrameShape(QtWidgets.QFrame.StyledPanel)
    review_layout = QtWidgets.QVBoxLayout(review_frame)
    review_layout.setContentsMargins(6, 6, 6, 6)
    review_layout.setSpacing(6)
    review_label = QtWidgets.QLabel("Review this hop")
    review_label.setStyleSheet("font-weight: bold;")
    review_layout.addWidget(review_label)
    review_edit = QtWidgets.QPlainTextEdit()
    review_edit.setFixedHeight(64)
    review_layout.addWidget(review_edit)
    review_btn_row = QtWidgets.QHBoxLayout()
    review_forward = QtWidgets.QPushButton("Forward")
    review_forward_edited = QtWidgets.QPushButton("Forward edited")
    review_skip = QtWidgets.QPushButton("Skip this hop")
    review_stop = QtWidgets.QPushButton("Stop relay")
    for btn in (review_forward, review_forward_edited,
                review_skip, review_stop):
        review_btn_row.addWidget(btn)
    review_btn_row.addStretch(1)
    review_layout.addLayout(review_btn_row)
    review_frame.setVisible(False)
    d_layout.addWidget(review_frame)

    relay_status = QtWidgets.QLabel("Relay off.")
    d_layout.addWidget(relay_status)
    root.addWidget(director)

    statusbar = window.statusBar()
    statusbar.showMessage("Loading models...")

    state = {"busy": False, "active": set(), "queued": set(), "relay": None,
             # Compare mode: the latest reply per side from a Send to
             # Both round. Complete when both sides are present.
             "compare": {}, "compare_round": False,
             # Edit-before-forward: the hop waiting on a review, or None.
             "pending_hop": None,
             # Last completed turn's stats per side (None until a turn
             # has run), and the side currently summarizing, if any.
             "stats": {"A": None, "B": None}, "summarizing": None}

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

    def render_transcript(side):
        """Rebuild a pane's transcript from its history and labels.

        Used after a reorder or delete. The format matches the live
        transcript ("You: ...", "<model>: ...", "You [relay ...]: ...");
        transient notes like "(Stopped.)" are not in the history, so a
        re-render drops them.
        """
        side_obj = convo.sides[side]
        lines = ["%s: %s" % (label, msg["content"])
                 for label, msg in zip(side_obj.labels, side_obj.history)]
        widgets[side]["transcript"].setPlainText(
            "\n".join(lines) + ("\n" if lines else ""))
        scroll_to_end(widgets[side]["transcript"])

    def refresh_history_list(side):
        """Rebuild a pane's history-editor list from its history."""
        lst = widgets[side]["hist_list"]
        lst.blockSignals(True)
        lst.clear()
        side_obj = convo.sides[side]
        for label, msg in zip(side_obj.labels, side_obj.history):
            text = msg["content"].replace("\n", " ")
            if len(text) > 70:
                text = text[:67] + "..."
            lst.addItem("%s: %s" % (label, text))
        lst.blockSignals(False)
        refresh_history_buttons(side)

    def refresh_history_buttons(side):
        """Enable the Up/Down/Delete buttons for the current selection.

        Editing is idle-only: never while this side is generating, a
        relay is running, or anything is busy.
        """
        w = widgets[side]
        lst = w["hist_list"]
        row = lst.currentRow()
        count = len(convo.sides[side].history)
        idle = (not state["busy"] and state.get("relay") is None
                and side not in state["active"]
                and side not in state["queued"])
        w["hist_up"].setEnabled(idle and row > 0)
        w["hist_down"].setEnabled(idle and 0 <= row < count - 1)
        w["hist_delete"].setEnabled(idle and row >= 0)

    def refresh_buttons():
        busy = state["busy"]
        relay = state.get("relay")
        relay_active = relay is not None and relay.active
        a_ready = convo.sides["A"].model is not None
        b_ready = convo.sides["B"].model is not None
        send_a.setEnabled(not busy and a_ready)
        send_b.setEnabled(not busy and b_ready)
        send_both.setEnabled(not busy and a_ready and b_ready)
        relay_btn.setText("Stop Relay" if relay_active else "Start Relay")
        relay_btn.setEnabled(
            relay_active or (not busy and a_ready and b_ready))
        for widget in (start_picker, cap_spin, mode_picker, stop_edit,
                       agree_check, review_check):
            widget.setEnabled(not relay_active)
        # Saving or loading a pair mid-generation could swap a model's
        # setup under a running turn, so pairs wait for idle too.
        pairs_idle = not busy and not relay_active
        pair_combo.setEnabled(pairs_idle)
        pair_name.setEnabled(pairs_idle)
        pair_save.setEnabled(pairs_idle)
        compare_ready = (not busy and not relay_active
                         and len(state.get("compare", {})) == 2)
        for side in ("A", "B"):
            w = widgets[side]
            w["picker"].setEnabled(not busy)
            w["refresh"].setEnabled(not busy)
            w["stop"].setEnabled(side in state["active"]
                                 or side in state["queued"])
            # No clearing either side mid-relay: the relay is writing
            # into both histories.
            w["clear"].setEnabled(not relay_active
                                  and side not in state["active"]
                                  and side not in state["queued"])
            w["use_this"].setEnabled(compare_ready)
            # Summarize: idle only, a model picked, and something older
            # to fold. While this side is summarizing the button says
            # so (it is disabled by the busy check above).
            summarizing = state.get("summarizing") == side
            w["summarize"].setText(
                "Summarizing..." if summarizing else "Summarize")
            w["summarize"].setEnabled(
                not busy and not relay_active
                and convo.sides[side].model is not None
                and len(convo.sides[side].history) > SUMMARY_KEEP_LAST)
            # The nudge shows only when summarizing can actually help:
            # over budget AND something older to fold. It never calls
            # the model on its own -- it only shows or hides.
            w["nudge"].setVisible(
                len(convo.sides[side].history) > SUMMARY_KEEP_LAST
                and convo.sides[side].history_chars()
                > HISTORY_BUDGET_CHARS)
            refresh_history_buttons(side)

    def set_status(side, text):
        widgets[side]["status"].setText(text)

    def refresh_stats_label(side):
        widgets[side]["stats"].setText(
            format_stats_line(state["stats"][side]))

    # --- runner wiring ----------------------------------------------------
    job_stats = {}  # job id -> {"metrics": dict|None, "ram": str|None}

    def _ram_text(model):
        """'<size> in RAM' for a running model, 'not loaded' when it
        isn't running, or None when the lookup itself failed (shown
        as 'RAM n/a')."""
        try:
            running = client.running_models()
        except Exception:
            return None
        if model in running:
            return "%s in RAM" % format_ram(running[model])
        return "not loaded"

    def make_job_fn(job_id, model, messages, track_stats=True):
        holder = {"metrics": None, "ram": None}
        job_stats[job_id] = holder

        def fn(emit, cancel_event):
            kwargs = {}
            if track_stats:
                kwargs["on_metrics"] = (
                    lambda m: holder.update(metrics=m))
            text = client.chat_stream(model, messages, emit, cancel_event,
                                      **kwargs)
            if track_stats:
                holder["ram"] = _ram_text(model)
            return text
        return fn

    def _emit(signal, *args):
        # The runner is a daemon thread and outlives the window: once
        # the window is gone its signal source is deleted and there is
        # nothing left to update. Swallow that instead of killing the
        # thread with a traceback.
        try:
            signal.emit(*args)
        except RuntimeError:
            pass

    runner = SerialRunner(
        on_start=lambda job: _emit(signals.started, job["id"]),
        on_chunk=lambda job, text: _emit(signals.chunk, job["id"], text),
        on_done=lambda job, text: _emit(signals.done, job["id"], text),
        on_error=lambda job, msg: _emit(signals.failed, job["id"], msg),
        on_cancelled=lambda job: _emit(signals.cancelled, job["id"]),
        on_idle=lambda: _emit(signals.idle),
    )

    def _job_side(job_id):
        """(side, is_summarize) for a runner job id.

        Normal turns use the side as the job id ("A"/"B"); summarize
        turns use "sum-A"/"sum-B".
        """
        if job_id.startswith("sum-"):
            return job_id[4:], True
        return job_id, False

    def on_started(job_id):
        if not alive():
            return
        side, is_summary = _job_side(job_id)
        if is_summary:
            # A summarize turn streams nothing into the transcript: the
            # summary lands as one entry when it finishes. The side is
            # deliberately NOT added to active/queued -- the per-side
            # Stop button cancels job id == side, which would poison
            # the next real turn for this side.
            set_status(side, "Summarizing...")
            refresh_buttons()
            return
        state["queued"].discard(side)
        state["active"].add(side)
        set_status(side, "Thinking...")
        widgets[side]["reply_open"] = True
        append_text(side, "%s: " % convo.sides[side].model)
        refresh_buttons()

    def on_chunk(job_id, text):
        if not alive():
            return
        side, is_summary = _job_side(job_id)
        if is_summary:
            return
        append_text(side, text)

    def finish_reply(side):
        if widgets[side]["reply_open"]:
            append_text(side, "\n")
            widgets[side]["reply_open"] = False

    def end_relay(reason, note_side=None):
        """End the active relay, showing why in the status line.

        note_side (optional) also gets a "(Relay stopped: ...)" line in
        its pane, so the stop is visible where the conversation ended.
        Any waiting hop review is dismissed with the relay.
        """
        session = state.get("relay")
        if session is None:
            return
        session.active = False
        session.stop_reason = reason
        state["relay"] = None
        state["pending_hop"] = None
        review_frame.setVisible(False)
        # If nothing is still generating or queued (e.g. the relay ended
        # from a review pause, where no job runs), the window is idle
        # again -- no on_idle signal will come to say so.
        if not state["active"] and not state["queued"]:
            state["busy"] = False
        relay_status.setText("Relay stopped: %s." % reason)
        if note_side is not None:
            append_text(note_side, "(Relay stopped: %s.)\n" % reason)
        refresh_buttons()

    def forward_hop(session, from_side, framed):
        """Relay one reply to the other side and start its turn.

        ``framed`` is exactly what the other model receives as a plain
        user message; the window shows the same text with a [relay] tag,
        which the models never see.
        """
        next_side = RelaySession.other(from_side)
        convo.sides[next_side].add_user(
            framed, label="You [relay from Model %s]" % from_side)
        append_text(next_side,
                    "You [relay from Model %s]: %s\n" % (from_side, framed))
        refresh_history_list(next_side)
        session.current_side = next_side
        session.hop_from = from_side
        state["queued"].add(next_side)
        set_status(next_side, "Queued...")
        relay_status.setText(session.status_running())
        runner.submit(next_side, make_job_fn(
            next_side, convo.sides[next_side].model,
            convo.sides[next_side].messages_for_model()))

    def pause_for_review(session, from_side, reply_text):
        """Park the relay on a hop for the user to review and edit.

        The relay stays active (so the window stays busy and no other
        send can start), but no job runs until the user forwards,
        skips, or stops from the review box.
        """
        next_side = RelaySession.other(from_side)
        framed = session.frame(reply_text, from_side)
        state["pending_hop"] = {"session": session, "from_side": from_side,
                                "framed": framed}
        review_label.setText(
            "Review this hop -- Model %s to Model %s (%s):"
            % (from_side, next_side, session.mode))
        review_edit.setPlainText(framed)
        review_frame.setVisible(True)
        relay_status.setText("Relay paused for review: turn %d/%d."
                             % (session.turns_done + 1, session.turn_cap))
        refresh_buttons()

    def do_forward(text):
        """Forward a reviewed hop with the given text and resume relaying."""
        hop = state.get("pending_hop")
        if hop is None:
            return
        state["pending_hop"] = None
        review_frame.setVisible(False)
        session = hop["session"]
        if session is None or not session.active:
            refresh_buttons()
            return
        forward_hop(session, hop["from_side"], text)
        refresh_buttons()

    def finish_summarize(side, text):
        """Apply a summarize turn: fold the older history into the
        summary entry. An empty summary changes nothing."""
        state["summarizing"] = None
        set_status(side, "Idle")
        if not text.strip():
            statusbar.showMessage(
                "Summarize came back empty — history unchanged.", 4000)
            refresh_buttons()
            return
        if convo.sides[side].summarize(text):
            render_transcript(side)
            refresh_history_list(side)
            statusbar.showMessage("History summarized.", 3000)
        refresh_buttons()

    def on_done(job_id, text):
        if not alive():
            return
        side, is_summary = _job_side(job_id)
        if is_summary:
            finish_summarize(side, text)
            return
        finish_reply(side)
        convo.sides[side].add_assistant(text)
        refresh_history_list(side)
        state["active"].discard(side)
        set_status(side, "Idle")
        # Stats for this turn (every turn, relay turns included). A
        # summarize turn never lands here, so it never overwrites the
        # last real turn's stats.
        holder = job_stats.pop(job_id, None)
        if holder is not None:
            stats = compute_turn_stats(holder.get("metrics") or {})
            stats["ram"] = holder.get("ram")
            state["stats"][side] = stats
            refresh_stats_label(side)
        session = state.get("relay")
        if session is not None and session.active:
            session.turns_done += 1
            reason = session.check_stop(text)
            if reason is not None:
                end_relay(reason, note_side=side)
            elif review_check.isChecked():
                pause_for_review(session, side, text)
            else:
                forward_hop(session, side, session.frame(text, side))
        elif state.get("compare_round"):
            # A Send to Both round: remember this side's reply so the
            # "Use this" buttons can offer it once both sides land.
            state["compare"][side] = text
        refresh_buttons()

    def on_failed(job_id, message):
        if not alive():
            return
        side, is_summary = _job_side(job_id)
        if is_summary:
            state["summarizing"] = None
            append_text(side, "(Summarize failed: %s)\n" % message)
            set_status(side, "Idle")
            refresh_buttons()
            return
        finish_reply(side)
        append_text(side, "(%s)\n" % message)
        state["active"].discard(side)
        set_status(side, "Idle")
        if state.get("relay") is not None:
            end_relay(message)
        refresh_buttons()

    def on_cancelled(job_id):
        if not alive():
            return
        side, is_summary = _job_side(job_id)
        if is_summary:
            # No button cancels a summarize turn, but stay safe if one
            # ever does: the history is simply left as it was.
            state["summarizing"] = None
            set_status(side, "Idle")
            refresh_buttons()
            return
        finish_reply(side)
        append_text(side, "(%s)\n" % MSG_STOPPED)
        state["active"].discard(side)
        state["queued"].discard(side)
        set_status(side, "Idle")
        if state.get("relay") is not None:
            end_relay(MSG_RELAY_STOPPED_BY_YOU)
        refresh_buttons()

    def on_idle():
        if not alive():
            return
        # A relay submits its next job from the done handler, which the
        # GUI processes before this idle signal -- but the runner may
        # still report idle between hops. Stay busy while a relay runs.
        if state.get("relay") is not None:
            refresh_buttons()
            return
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
        # A new send starts a new round: any earlier compare offer ends.
        state["compare"] = {}
        state["compare_round"] = (action == "Both")
        composer.clear()
        for side_obj in targets:
            side = side_obj.name
            side_obj.add_user(text)
            append_text(side, "You: %s\n" % text)
            refresh_history_list(side)
            state["queued"].add(side)
            set_status(side, "Queued...")
            runner.submit(side, make_job_fn(
                side, side_obj.model, side_obj.messages_for_model()))
        refresh_buttons()
        composer.setFocus()

    send_a.clicked.connect(lambda: do_send("A"))
    send_b.clicked.connect(lambda: do_send("B"))
    send_both.clicked.connect(lambda: do_send("Both"))

    # --- summarize --------------------------------------------------------
    def do_summarize(side):
        """Ask this side's own model to summarize its conversation,
        then fold the older turns into the summary entry. One extra
        model turn, run through the same one-at-a-time runner; the
        summary request itself never enters the history."""
        if state["busy"] or state.get("relay") is not None:
            return
        side_obj = convo.sides[side]
        if side_obj.model is None:
            statusbar.showMessage(
                MSG_NO_MODEL.format(side="Model " + side), 4000)
            return
        if len(side_obj.history) <= SUMMARY_KEEP_LAST:
            statusbar.showMessage("Nothing to summarize yet.", 3000)
            return
        state["busy"] = True
        state["summarizing"] = side
        # A summarize turn is not a compare round: the offer is spent.
        state["compare"] = {}
        state["compare_round"] = False
        messages = side_obj.messages_for_model() + [
            {"role": "user", "content": SUMMARIZE_PROMPT}]
        set_status(side, "Summarizing...")
        runner.submit("sum-" + side, make_job_fn(
            "sum-" + side, side_obj.model, messages, track_stats=False))
        refresh_buttons()

    # --- relay ------------------------------------------------------------
    def do_start_relay():
        if state.get("relay") is not None or state["busy"]:
            return
        text = composer.toPlainText().strip()
        if not text:
            statusbar.showMessage(MSG_EMPTY, 3000)
            return
        if (convo.sides["A"].model is None
                or convo.sides["B"].model is None):
            statusbar.showMessage(MSG_RELAY_NEEDS_BOTH, 4000)
            return
        start_side = start_picker.currentText()
        session = RelaySession(
            start_side=start_side,
            turn_cap=cap_spin.value(),
            mode=("aware" if mode_picker.currentText() == "Aware"
                  else "blind"),
            stop_phrases=parse_stop_phrases(stop_edit.text()),
            stop_on_agreement=agree_check.isChecked(),
        )
        state["relay"] = session
        state["busy"] = True
        # A relay is not a compare round.
        state["compare"] = {}
        state["compare_round"] = False
        composer.clear()
        side_obj = convo.sides[start_side]
        side_obj.add_user(text)
        append_text(start_side, "You: %s\n" % text)
        refresh_history_list(start_side)
        state["queued"].add(start_side)
        set_status(start_side, "Queued...")
        relay_status.setText(session.status_running())
        runner.submit(start_side, make_job_fn(
            start_side, side_obj.model, side_obj.messages_for_model()))
        refresh_buttons()
        composer.setFocus()

    def stop_relay():
        session = state.get("relay")
        if session is None or not session.active:
            return
        current = session.current_side
        end_relay(MSG_RELAY_STOPPED_BY_YOU)
        # Cancel the in-flight job, if there is one. Its cancel lands in
        # on_cancelled, which finds no active relay and just reports
        # "(Stopped.)". During a review pause no job runs, and
        # cancelling a phantom id would poison the next job submitted
        # for that side.
        if current in state["active"] or current in state["queued"]:
            runner.cancel(current)

    def on_relay_button():
        if state.get("relay") is not None:
            stop_relay()
        else:
            do_start_relay()

    relay_btn.clicked.connect(on_relay_button)

    # --- hop review --------------------------------------------------------
    def on_review_forward():
        hop = state.get("pending_hop")
        if hop is not None:
            do_forward(hop["framed"])

    def on_review_forward_edited():
        text = review_edit.toPlainText()
        if not text.strip():
            statusbar.showMessage(MSG_REVIEW_EMPTY, 3000)
            return
        do_forward(text)

    def on_review_skip():
        hop = state.get("pending_hop")
        from_side = hop["from_side"] if hop is not None else None
        end_relay("hop skipped by you", note_side=from_side)

    review_forward.clicked.connect(on_review_forward)
    review_forward_edited.clicked.connect(on_review_forward_edited)
    review_skip.clicked.connect(on_review_skip)
    review_stop.clicked.connect(on_relay_button)

    # --- compare mode -------------------------------------------------------
    def use_this(side):
        """Continue from this side's reply: send it to the other side.

        Only offered after a Send to Both round, once both replies are
        in. The other side gets "You picked this answer: <reply>" as a
        user message and answers it; the picked pane is marked.
        """
        if state.get("busy") or state.get("relay") is not None:
            return
        text = state.get("compare", {}).get(side)
        if text is None:
            return
        other = RelaySession.other(side)
        if convo.sides[other].model is None:
            return
        picked = COMPARE_PREFIX % text
        state["compare"] = {}
        state["compare_round"] = False
        state["busy"] = True
        convo.sides[other].add_user(picked)
        append_text(other, "You: %s\n" % picked)
        refresh_history_list(other)
        append_text(side, "(Picked for Model %s.)\n" % other)
        state["queued"].add(other)
        set_status(other, "Queued...")
        runner.submit(other, make_job_fn(
            other, convo.sides[other].model,
            convo.sides[other].messages_for_model()))
        refresh_buttons()

    # --- history editor -----------------------------------------------------
    def make_hist_move(side, direction):
        def move():
            if state["busy"] or state.get("relay") is not None:
                return
            lst = widgets[side]["hist_list"]
            row = lst.currentRow()
            if row < 0:
                return
            side_obj = convo.sides[side]
            if direction < 0:
                side_obj.move_up(row)
                new_row = max(0, row - 1)
            else:
                side_obj.move_down(row)
                new_row = min(len(side_obj.history) - 1, row + 1)
            render_transcript(side)
            refresh_history_list(side)
            lst.setCurrentRow(new_row)
            refresh_buttons()
        return move

    def make_hist_delete(side):
        def delete():
            if state["busy"] or state.get("relay") is not None:
                return
            lst = widgets[side]["hist_list"]
            row = lst.currentRow()
            if row < 0:
                return
            convo.sides[side].delete_at(row)
            render_transcript(side)
            refresh_history_list(side)
            if convo.sides[side].history:
                lst.setCurrentRow(
                    min(row, len(convo.sides[side].history) - 1))
            refresh_buttons()
        return delete

    def make_instr_toggle(side):
        def toggled(checked):
            widgets[side]["instr_edit"].setVisible(checked)
            widgets[side]["instr_toggle"].setText(
                "Instructions ▾" if checked else "Instructions ▸")
        return toggled

    def make_hist_toggle(side):
        def toggled(checked):
            widgets[side]["hist_frame"].setVisible(checked)
            widgets[side]["hist_toggle"].setText(
                "History ▾" if checked else "History ▸")
            if checked:
                refresh_history_list(side)
        return toggled

    def make_instr_changed(side):
        def changed():
            convo.sides[side].instruction = (
                widgets[side]["instr_edit"].toPlainText())
        return changed

    for side in ("A", "B"):
        w = widgets[side]
        w["use_this"].clicked.connect(
            (lambda s: lambda: use_this(s))(side))
        w["hist_up"].clicked.connect(make_hist_move(side, -1))
        w["hist_down"].clicked.connect(make_hist_move(side, +1))
        w["hist_delete"].clicked.connect(make_hist_delete(side))
        w["hist_list"].currentRowChanged.connect(
            (lambda s: lambda *a: refresh_history_buttons(s))(side))
        w["instr_toggle"].toggled.connect(make_instr_toggle(side))
        w["hist_toggle"].toggled.connect(make_hist_toggle(side))
        w["instr_edit"].textChanged.connect(make_instr_changed(side))

    # --- saved pairs ---------------------------------------------------------
    def refresh_pair_combo(select=None):
        """Reload the pair dropdown from the JSON file. Nothing is
        loaded by default -- the dropdown just lists what is saved."""
        pairs = load_pairs(pairs_path())
        pair_combo.blockSignals(True)
        pair_combo.clear()
        names = sorted(pairs)
        pair_combo.addItems(names)
        if select in names:
            pair_combo.setCurrentIndex(names.index(select))
        else:
            pair_combo.setCurrentIndex(-1)
        pair_combo.blockSignals(False)
        return pairs

    def on_pair_save():
        name = pair_name.text().strip()
        if not name:
            statusbar.showMessage(MSG_PAIR_NEEDS_NAME, 3000)
            return
        setup = {
            "model_a": convo.sides["A"].model,
            "model_b": convo.sides["B"].model,
            "instruction_a": widgets["A"]["instr_edit"].toPlainText(),
            "instruction_b": widgets["B"]["instr_edit"].toPlainText(),
        }
        save_pair(pairs_path(), name, setup)
        refresh_pair_combo(select=name)
        statusbar.showMessage("Pair '%s' saved." % name, 3000)

    def on_pair_selected(index):
        if index < 0:
            return
        name = pair_combo.currentText()
        setup = load_pairs(pairs_path()).get(name)
        if setup is None:
            return
        missing = []
        for side, key in (("A", "model_a"), ("B", "model_b")):
            model = setup.get(key)
            if not model:
                continue
            picker = widgets[side]["picker"]
            found = picker.findText(model)
            if found >= 0:
                picker.setCurrentIndex(found)
            else:
                missing.append(model)
        widgets["A"]["instr_edit"].setPlainText(
            setup.get("instruction_a") or "")
        widgets["B"]["instr_edit"].setPlainText(
            setup.get("instruction_b") or "")
        if missing:
            statusbar.showMessage(
                "Pair '%s' loaded, but not installed: %s."
                % (name, ", ".join(missing)), 5000)
        else:
            statusbar.showMessage("Pair '%s' loaded." % name, 3000)
        refresh_buttons()

    pair_save.clicked.connect(on_pair_save)
    # Both signals: currentIndexChanged covers picking a different pair,
    # activated covers re-picking the pair that is already selected
    # (choosing the same item again fires no index change, but the user
    # still expects the load). Loading twice is harmless -- it applies
    # the same setup.
    pair_combo.currentIndexChanged.connect(on_pair_selected)
    pair_combo.activated.connect(on_pair_selected)

    # --- per-side buttons --------------------------------------------------
    def make_stop(side):
        def stop():
            runner.cancel(side)
        return stop

    def make_summarize(side):
        def summarize():
            do_summarize(side)
        return summarize

    def make_clear(side):
        def clear():
            convo.sides[side].clear()
            widgets[side]["transcript"].clear()
            refresh_history_list(side)
            # A cleared side ends any compare offer: the pair is broken.
            state["compare"] = {}
            state["compare_round"] = False
            # ...and its last turn's stats no longer describe anything.
            state["stats"][side] = None
            refresh_stats_label(side)
            set_status(side, "Idle")
            refresh_buttons()
        return clear

    for side in ("A", "B"):
        widgets[side]["stop"].clicked.connect(make_stop(side))
        widgets[side]["summarize"].clicked.connect(make_summarize(side))
        widgets[side]["clear"].clicked.connect(make_clear(side))

    # --- model pickers -----------------------------------------------------
    def on_picker_changed(side):
        def changed(index):
            picker = widgets[side]["picker"]
            convo.sides[side].model = (
                picker.currentText() if index >= 0 else None)
            # A different model makes the last turn's stats stale.
            state["stats"][side] = None
            refresh_stats_label(side)
            refresh_buttons()
        return changed

    for side in ("A", "B"):
        widgets[side]["picker"].currentIndexChanged.connect(
            on_picker_changed(side))

    def populate_models(models):
        if not alive():
            return
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
        if not alive():
            return
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
        "relay_btn": relay_btn, "start_picker": start_picker,
        "cap_spin": cap_spin, "mode_picker": mode_picker,
        "stop_edit": stop_edit, "agree_check": agree_check,
        "relay_status": relay_status,
        "do_start_relay": do_start_relay, "stop_relay": stop_relay,
        "review_check": review_check, "review_frame": review_frame,
        "review_label": review_label, "review_edit": review_edit,
        "review_forward": review_forward,
        "review_forward_edited": review_forward_edited,
        "review_skip": review_skip, "review_stop": review_stop,
        "pair_combo": pair_combo, "pair_name": pair_name,
        "pair_save": pair_save, "refresh_pair_combo": refresh_pair_combo,
        "use_this": use_this, "do_forward": do_forward,
        "render_transcript": render_transcript,
        "refresh_history_list": refresh_history_list,
        "do_summarize": do_summarize, "refresh_buttons": refresh_buttons,
    }

    refresh_pair_combo()
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
