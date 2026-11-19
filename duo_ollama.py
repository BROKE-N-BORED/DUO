"""Ollama over localhost HTTP: the client and its retry rules.
Stdlib only.

Split out of dual_chat.py in v0.25 (audit T14). The retry
constants live here: the client reads them from this module.

v0.49: the client now matches Code Forge's robustness -- a
transient HTTP 500 (the llama-server start timeout) is retried
like a dropped connection, every chat request carries
keep_alive "30m" so models stay loaded for the session, the
model list is retried before falling back, and the final error
is decided by what the attempts actually failed with, not by a
fresh probe that can itself fail under load. The Start Ollama
button and the server manager are gone: DUO no longer starts
Ollama, it stays connected to the one that is running.

v0.52: Stop can interrupt a stalled generation. The read loop
used to block up to CHAT_TIMEOUT (30 min) inside one readline
with the cancel check only before it, so Stop did nothing for
up to 90 min across the three attempts. Now a reader thread
blocks in readline and the main loop takes lines from a queue
in short waits, checking Stop and the silence budget on every
quiet window; a genuine stall still fails the attempt after
CHAT_TIMEOUT of silence, exactly as before. The socket timeout
is deliberately NOT shortened: a socket makefile cannot be
read again after a timeout (CPython raises "cannot read from
timed out object"), which a first version of this fix learned
the hard way. And the response is NOT simply closed at the
end: closing while the reader is inside readline can block
(probed: fp.close() waits on the blocked read), so the socket
is shut down first, the reader is joined, and only then is
the response closed.
"""

import json
import os
import queue
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


OLLAMA_URL_ENV = "DUAL_CHAT_OLLAMA_URL"


MSG_OLLAMA_DOWN = "Ollama isn't running. Start Ollama and try again."


MSG_OLLAMA_SLOW = "Ollama is slow or busy right now. Try again in a moment."


MSG_MODEL_FAILED = "That model couldn't be loaded. Pick another model and try again."


# v0.58 (P2-52): the runner's catch-all used to report every
# unexpected failure as MSG_MODEL_FAILED, so any hiccup read as
# "that model couldn't be loaded" even when the model was fine.
# An unexpected failure gets honest, general words instead.
MSG_TURN_FAILED = "Something went wrong with that turn. Try again."


# DUO used to wait 300s -- on a slow CPU, Ollama's HTTP server can
# stall briefly between relay hops while the CPU is pegged at 100%
# generating, and DUO gave up on the first failure with no retry. Now
# a turn retries transient failures (connect/read before the first
# content chunk) a few times with a pause between attempts, and only
# then fails. The pauses are interruptible: Stop ends them at once.
CHAT_TIMEOUT = 1800


# The read loop's wake-up interval (v0.52, P0-1): the main loop
# takes lines from the reader thread's queue in waits of this
# length, checking Stop and the silence budget on every quiet
# window. Five seconds keeps Stop responsive without churning
# on a quiet stream; a slow-but-alive stream is untouched,
# because every line that arrives restarts the silence budget.
CHAT_READ_TIMEOUT = 5


CHAT_MAX_ATTEMPTS = 3


CHAT_RETRY_DELAYS = (2.0, 5.0)  # pause before attempt 2, then attempt 3


# Code Forge sends keep_alive "30m" on every chat request (its Audit
# B20): Ollama's 5-minute default unloads the model during any wait
# past five minutes, and the next generation pays a full reload from
# disk. DUO sends the same value, so a model stays loaded for the
# session instead of unloading between turns.
CHAT_KEEP_ALIVE = "30m"


# StartFix (Code Forge, 2026-11-11): Ollama refusing a generation
# with HTTP 500 "timed out waiting for llama-server to start" means
# it gave up waiting for the model's runner process to come up --
# on a hard-drive machine, usually while the disk is busy loading
# or swapping models. The failure is transient and precedes any
# token, so it is retried like a dropped connection. Any other HTTP
# error is still a plain model failure, never retried.
LLAMA_START_TIMEOUT_MARKER = "timed out waiting for llama-server to start"


# The model list (/api/tags) gets the same patience as a turn: one
# 5s attempt under a pegged CPU is not enough evidence that Ollama
# is down. Three attempts at Code Forge's 8s timeout, then the
# `ollama list` fallback.
TAGS_TIMEOUT = 8


TAGS_MAX_ATTEMPTS = 3


TAGS_RETRY_DELAYS = (1.0, 2.0)  # pause before attempt 2, then attempt 3


class OllamaError(Exception):
    """A plain-language Ollama failure. ``message`` is safe to show."""


class Cancelled(Exception):
    """Raised inside a generation when the user stops it."""


def parse_tags_payload(payload):
    """Parse an /api/tags JSON payload into a sorted list of model names."""
    names = []
    for entry in payload.get("models", []) or []:
        name = entry.get("name")
        if name:
            names.append(name)
    return sorted(names)


def parse_tags_sizes(payload):
    """Parse an /api/tags JSON payload into a sorted list of
    (name, size-in-bytes) pairs. A missing or non-numeric size is
    None -- the model still lists, just without a size. Entries
    without a name are skipped."""
    pairs = []
    for entry in payload.get("models", []) or []:
        name = entry.get("name")
        if not name:
            continue
        size = entry.get("size")
        if not isinstance(size, (int, float)):
            size = None
        pairs.append((name, size))
    return sorted(pairs)


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


# --- Update check (v0.28, audit T17) ---------------------------------------
# DUO tells the user when a newer Release exists. Since v0.55 the
# popup offers to pull it (duo_updater.py) -- this check only ever
# looks, and never downloads anything itself.
# The source of truth is the GitHub API's latest-release endpoint
# for this repo, unauthenticated, with a short timeout so a slow
# network never holds the app up. DUO_UPDATE_CHECK_URL points the
# check at a different endpoint (for testing only).

UPDATE_CHECK_URL = (
    "https://api.github.com/repos/BROKE-N-BORED/DUO/releases/latest")


UPDATE_CHECK_URL_ENV = "DUO_UPDATE_CHECK_URL"


UPDATE_CHECK_TIMEOUT = 5


def update_check_url():
    """The endpoint check_for_update asks: the GitHub latest-release
    URL, or the DUO_UPDATE_CHECK_URL override when one is set."""
    return os.environ.get(UPDATE_CHECK_URL_ENV) or UPDATE_CHECK_URL


def parse_version(text):
    """Parse a version like "0.28" or "v0.28" into a tuple of ints
    for numeric comparison ("0.9" < "0.10" holds). Returns None
    when the text is not a dotted-number version -- empty, a
    non-number part, trailing junk -- so a bad tag can never crash
    the check or report a nonsense update."""
    if not isinstance(text, str):
        return None
    text = text.strip()
    if text.startswith(("v", "V")):
        text = text[1:]
    if not text:
        return None
    numbers = []
    for part in text.split("."):
        if not part.isdigit():
            return None
        numbers.append(int(part))
    return tuple(numbers)


def check_for_update(current_version, url=None, timeout=UPDATE_CHECK_TIMEOUT,
                     urlopen=None):
    """Ask for the latest DUO release and compare with
    ``current_version``. Returns ("newer", version) when a newer
    release exists (version is the normalized dotted number, e.g.
    "0.29"), ("latest", None) when this copy is current, and
    ("failed", None) when the check could not be completed -- no
    network, a timeout, a bad response, or a tag that doesn't parse.
    Never raises: any failure is ("failed", None), so the caller
    only ever has to decide what to show. ``urlopen`` is the
    url-opener to use (urllib's by default; tests pass a stub)."""
    current = parse_version(current_version)
    if current is None:
        return ("failed", None)
    if url is None:
        url = update_check_url()
    if urlopen is None:
        urlopen = urllib.request.urlopen
    try:
        request = urllib.request.Request(
            url, headers={"Accept": "application/vnd.github+json",
                          "User-Agent": "DUO-update-check"})
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return ("failed", None)
    tag = payload.get("tag_name") if isinstance(payload, dict) else None
    latest = parse_version(tag)
    if latest is None:
        return ("failed", None)
    if latest > current:
        return ("newer", ".".join(str(n) for n in latest))
    return ("latest", None)


def start_ollama():
    """Launch `ollama serve` detached (v0.58, P2-53).

    The down state's remedy used to be a terminal command; under
    the no-terminal rule the app starts the service itself, on a
    click. The process is detached (its own session, output
    dropped) so it outlives DUO and never holds DUO's console.
    Returns True when the process was spawned, False when ollama
    is not installed or the spawn failed. Spawning proves nothing
    about readiness: the caller waits, then re-checks the model
    list, which is the real test.
    """
    exe = shutil.which("ollama")
    if exe is None:
        return False
    try:
        subprocess.Popen(
            [exe, "serve"], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
    except Exception:
        return False
    return True


def llama_start_timeout(detail):
    """True when an Ollama error body is the llama-server start
    timeout -- Ollama's own wording, matched on the body text."""
    return LLAMA_START_TIMEOUT_MARKER in (detail or "").lower()


def _shutdown_response(resp):
    """Shut down the socket under `resp` so a reader thread
    blocked in readline wakes at once (v0.52, P0-1). Closing the
    response while the reader is inside readline can block --
    probed: fp.close() waits on the blocked read -- so the
    shutdown runs first, the reader is joined, and only then is
    the response closed. Best-effort: a response with no
    reachable socket (a fake, or a future urllib that
    rearranges the chain) is left alone, and a socket that is
    already closed just raises OSError, which is swallowed.
    Returns True when a socket was shut down."""
    try:
        sock = resp.fp.raw._sock
    except AttributeError:
        return False
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        return False
    return True


def _ollama_failure_kind(exc):
    """Classify a chat failure: "down" when Ollama refused the
    connection (nothing listening), "slow" when it timed out (up but
    not answering in time), "error" for anything else. urllib wraps
    the real error in URLError.reason, so walk the chain."""
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ConnectionRefusedError):
            return "down"
        if isinstance(current, TimeoutError):
            return "slow"
        current = (getattr(current, "reason", None)
                   or getattr(current, "__cause__", None))
    return "error"


class _Retryable(Exception):
    """A chat attempt failed before any content streamed: safe to try
    again. Carries the original error and its failure kind."""

    def __init__(self, error, kind):
        super().__init__(str(error))
        self.error = error
        self.kind = kind


class OllamaClient:
    """Minimal Ollama client over localhost HTTP. Stdlib only."""

    def __init__(self, base_url=None):
        self.base_url = (base_url or os.environ.get(OLLAMA_URL_ENV)
                         or DEFAULT_OLLAMA_URL).rstrip("/")

    def _fetch_tags_payload(self, timeout=TAGS_TIMEOUT):
        """The /api/tags JSON payload, retried like a turn: a slow
        Ollama under load can miss one short attempt, and that is
        not evidence it is down. Raises the last error when every
        attempt fails, so the caller falls back to `ollama list`."""
        last_error = None
        for attempt in range(1, TAGS_MAX_ATTEMPTS + 1):
            try:
                req = urllib.request.Request(self.base_url + "/api/tags")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as exc:  # noqa: BLE001 - retried, then fallback
                last_error = exc
                if attempt < TAGS_MAX_ATTEMPTS:
                    time.sleep(TAGS_RETRY_DELAYS[attempt - 1])
        raise last_error

    def list_models(self, timeout=TAGS_TIMEOUT):
        """Return installed model names, or raise OllamaError."""
        try:
            return parse_tags_payload(self._fetch_tags_payload(timeout))
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

    def list_models_with_sizes(self, timeout=TAGS_TIMEOUT):
        """Return installed models as [(name, size-in-bytes), ...],
        sorted by name, or raise OllamaError. Sizes come from
        /api/tags; the `ollama list` fallback has no sizes, so its
        entries carry None. A model with no size still lists -- the
        picker shows its name without a size."""
        try:
            return parse_tags_sizes(self._fetch_tags_payload(timeout))
        except Exception:
            pass  # Fall through to the `ollama list` fallback.
        try:
            if shutil.which("ollama") is None:
                raise OllamaError(MSG_OLLAMA_DOWN)
            out = subprocess.run(
                ["ollama", "list"], capture_output=True, text=True, timeout=15)
            if out.returncode != 0:
                raise OllamaError(MSG_OLLAMA_DOWN)
            return [(name, None)
                    for name in parse_ollama_list_output(out.stdout)]
        except OllamaError:
            raise
        except Exception:
            raise OllamaError(MSG_OLLAMA_DOWN)

    def chat_stream(self, model, messages, on_chunk, cancel_event,
                    timeout=CHAT_TIMEOUT, on_metrics=None, on_retry=None,
                    options=None):
        """Stream one chat turn. Calls on_chunk(text) per delta.

        Returns the full reply text. Raises Cancelled if cancel_event is
        set, OllamaError with a plain message on failure.

        ``options`` (optional) is Ollama's options dict for the turn --
        the GUI passes {"temperature": <the side's value>}. When None,
        no options key is sent at all, so Ollama uses its defaults.

        on_metrics (optional) is called once at the end with the turn's
        timing: eval_count, eval_duration_ns, and prompt_eval_count
        from the streamed done chunk, plus client-side ttft_s
        (request start to first content chunk) and total_s. Missing
        fields are None. Both timings span the whole turn, retries
        included -- that is what the user waited.

        on_retry (optional) is called before each retry with
        (attempt, delay_seconds, kind): attempt is the attempt that
        just failed (1-based), kind is "down", "slow", or "error".

        Retry rule: failures BEFORE the first content chunk (connect
        refused, timeout waiting for the first token, connection cut
        early, and Ollama's transient HTTP 500 "timed out waiting
        for llama-server to start") are retried up to
        CHAT_MAX_ATTEMPTS times, with a pause between attempts that
        Stop interrupts at once. Once content has streamed, a
        failure ends the turn: the partial text the caller already
        received stays, and the turn fails with the slow/busy
        message -- never retry a half-streamed turn, it would
        duplicate the partial reply.
        """
        started = time.monotonic()
        for attempt in range(1, CHAT_MAX_ATTEMPTS + 1):
            if cancel_event is not None and cancel_event.is_set():
                raise Cancelled()
            try:
                return self._chat_attempt(
                    model, messages, on_chunk, cancel_event,
                    timeout, started, on_metrics, options)
            except _Retryable as exc:
                if attempt >= CHAT_MAX_ATTEMPTS:
                    raise self._final_ollama_error(exc)
                delay = CHAT_RETRY_DELAYS[attempt - 1]
                if on_retry is not None:
                    on_retry(attempt, delay, exc.kind)
                # Interruptible pause: Stop ends the wait at once
                # instead of sleeping through it.
                if (cancel_event is not None
                        and cancel_event.wait(delay)):
                    raise Cancelled()
        raise OllamaError(MSG_OLLAMA_DOWN)  # unreachable

    def _final_ollama_error(self, retryable):
        """The plain error for a turn that exhausted its retries.

        The last attempt's failure kind decides: a refused
        connection means nothing is listening (Ollama is down); a
        timeout or any other failure means Ollama was up but not
        answering (slow or busy). No fresh probe -- under a pegged
        CPU the probe itself can fail and misreport a busy Ollama
        as down."""
        if retryable.kind == "down":
            return OllamaError(MSG_OLLAMA_DOWN)
        return OllamaError(MSG_OLLAMA_SLOW)

    def _chat_attempt(self, model, messages, on_chunk, cancel_event,
                      timeout, started, on_metrics, options=None):
        """One try at a chat turn. Returns the full reply text.

        Raises Cancelled, OllamaError (model failure, or a mid-stream
        failure after content already streamed), or _Retryable (a
        failure before any content streamed -- safe to try again).
        """
        payload = {"model": model, "messages": messages, "stream": True,
                   "keep_alive": CHAT_KEEP_ALIVE}
        if options is not None:
            payload["options"] = options
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # Ollama answered with a status. One 500 is transient:
            # the llama-server start timeout (Code Forge's StartFix)
            # means the model wasn't up yet -- retry it like a
            # dropped connection. Anything else is a model failure
            # a retry cannot fix.
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            except Exception:
                detail = ""
            if llama_start_timeout(detail):
                raise _Retryable(exc, "slow")
            raise OllamaError(MSG_MODEL_FAILED)
        except Exception as exc:
            raise _Retryable(exc, _ollama_failure_kind(exc))
        full = []
        ttft = None
        final = {}
        # v0.52 (P0-1): read the stream on a reader thread and
        # take lines from a queue in short waits, so Stop can
        # land while the server is quiet. The reader thread
        # blocks in readline with the long socket timeout it
        # always had -- shortening it is not an option, because
        # a socket makefile cannot be read again after a timeout
        # (CPython raises "cannot read from timed out object").
        # The main loop wakes every CHAT_READ_TIMEOUT seconds to
        # check Stop and the silence budget; a slow-but-alive
        # stream is untouched, because every line that arrives
        # restarts the budget. The finally below shuts the
        # socket down before closing the response: closing
        # while the reader is inside readline can block
        # (probed), and the shutdown wakes the reader at once,
        # so no thread is left behind; it is a daemon either way.
        # The queue carries lines (bytes), b"" at end of stream,
        # or the exception a failed readline raised.
        lines = queue.Queue()

        def _read_lines():
            while True:
                try:
                    line = resp.readline()
                except Exception as exc:
                    lines.put(exc)
                    return
                lines.put(line)
                if not line:
                    return

        reader = threading.Thread(target=_read_lines, daemon=True)
        reader.start()
        last_data = time.monotonic()
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise Cancelled()
                try:
                    item = lines.get(timeout=CHAT_READ_TIMEOUT)
                except queue.Empty:
                    # One quiet window, not a failure yet: a slow
                    # model can pause between tokens. Stop lands
                    # here within one window.
                    if cancel_event is not None and cancel_event.is_set():
                        raise Cancelled()
                    if time.monotonic() - last_data >= timeout:
                        exc = TimeoutError("timed out")
                        if full:
                            raise OllamaError(MSG_OLLAMA_SLOW)
                        raise _Retryable(exc, _ollama_failure_kind(exc))
                    continue
                if isinstance(item, Exception):
                    if full:
                        # Content already streamed: keep the partial
                        # text, fail the turn, never retry.
                        raise OllamaError(MSG_OLLAMA_SLOW)
                    raise _Retryable(item, _ollama_failure_kind(item))
                line = item
                last_data = time.monotonic()
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
                    # v0.58 (P2-52): an error chunk before any
                    # content is the model failing to start, but an
                    # error chunk after content already streamed is
                    # a mid-turn failure, not a load failure -- the
                    # words must not blame the model for it.
                    if full:
                        raise OllamaError(MSG_OLLAMA_SLOW)
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
            # Shut the socket down before closing: closing the
            # response while the reader is inside readline can
            # block (probed: fp.close() waits on the blocked
            # read), and the shutdown wakes the reader at once.
            # Join first so the reader is out of readline before
            # the close runs; the join is bounded, so a socket
            # that could not be reached degrades to the old
            # close instead of hanging here.
            _shutdown_response(resp)
            reader.join(timeout=CHAT_READ_TIMEOUT)
            resp.close()
        if cancel_event is not None and cancel_event.is_set():
            raise Cancelled()
        if on_metrics is not None:
            on_metrics({
                "eval_count": final.get("eval_count"),
                "eval_duration_ns": final.get("eval_duration"),
                "prompt_eval_count": final.get("prompt_eval_count"),
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
