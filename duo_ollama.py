"""Ollama over localhost HTTP: the client, its retry rules,
and the server manager. Stdlib only.

Split out of dual_chat.py in v0.25 (audit T14). The retry
constants live here: the client reads them from this module.
"""

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"


OLLAMA_URL_ENV = "DUAL_CHAT_OLLAMA_URL"


MSG_OLLAMA_DOWN = "Ollama isn't running. Start Ollama and try again."


MSG_OLLAMA_SLOW = "Ollama is slow or busy right now. Try again in a moment."


MSG_OLLAMA_NOT_INSTALLED = (
    "Ollama isn't installed on this computer, so DUO can't start it.")


MSG_OLLAMA_START_FAILED = (
    "Ollama didn't start. Try starting it yourself and try again.")


MSG_MODEL_FAILED = "That model couldn't be loaded. Pick another model and try again."


# DUO used to wait 300s -- on a slow CPU, Ollama's HTTP server can
# stall briefly between relay hops while the CPU is pegged at 100%
# generating, and DUO gave up on the first failure with no retry. Now
# a turn retries transient failures (connect/read before the first
# content chunk) a few times with a pause between attempts, and only
# then fails. The pauses are interruptible: Stop ends them at once.
CHAT_TIMEOUT = 1800


CHAT_MAX_ATTEMPTS = 3


CHAT_RETRY_DELAYS = (2.0, 5.0)  # pause before attempt 2, then attempt 3


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
# DUO tells the user when a newer Release exists. It never downloads
# or installs anything -- the user downloads from GitHub themselves.
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

    def list_models_with_sizes(self, timeout=5):
        """Return installed models as [(name, size-in-bytes), ...],
        sorted by name, or raise OllamaError. Sizes come from
        /api/tags; the `ollama list` fallback has no sizes, so its
        entries carry None. A model with no size still lists -- the
        picker shows its name without a size."""
        try:
            req = urllib.request.Request(self.base_url + "/api/tags")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return parse_tags_sizes(json.loads(resp.read().decode("utf-8")))
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

    def probe(self, timeout=5):
        """True when Ollama answers /api/tags, False otherwise.

        A fast liveness check: used between retries and by the Start
        Ollama wait loop. Never raises."""
        try:
            req = urllib.request.Request(self.base_url + "/api/tags")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read()
            return True
        except Exception:
            return False

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
        early) are retried up to CHAT_MAX_ATTEMPTS times, with a pause
        between attempts that Stop interrupts at once. Once content
        has streamed, a failure ends the turn: the partial text the
        caller already received stays, and the turn fails with the
        slow/busy message -- never retry a half-streamed turn, it
        would duplicate the partial reply.
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

        A fresh probe decides the message: Ollama answering now means
        it was slow or busy, not down -- never call a slow Ollama
        "not running". Only a probe that fails too earns the down
        message."""
        if self.probe():
            return OllamaError(MSG_OLLAMA_SLOW)
        return OllamaError(MSG_OLLAMA_DOWN)

    def _chat_attempt(self, model, messages, on_chunk, cancel_event,
                      timeout, started, on_metrics, options=None):
        """One try at a chat turn. Returns the full reply text.

        Raises Cancelled, OllamaError (model failure, or a mid-stream
        failure after content already streamed), or _Retryable (a
        failure before any content streamed -- safe to try again).
        """
        payload = {"model": model, "messages": messages, "stream": True}
        if options is not None:
            payload["options"] = options
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/api/chat", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # Ollama answered with a status: it is up, and a missing
            # model will not fix itself by retrying.
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("error", "")
            except Exception:
                detail = ""
            if "not found" in detail.lower() or "model" in detail.lower():
                raise OllamaError(MSG_MODEL_FAILED)
            raise OllamaError(MSG_MODEL_FAILED)
        except Exception as exc:
            raise _Retryable(exc, _ollama_failure_kind(exc))
        full = []
        ttft = None
        final = {}
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise Cancelled()
                try:
                    line = resp.readline()
                except Exception as exc:
                    if full:
                        # Content already streamed: keep the partial
                        # text, fail the turn, never retry.
                        raise OllamaError(MSG_OLLAMA_SLOW)
                    raise _Retryable(exc, _ollama_failure_kind(exc))
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


class OllamaServerManager:
    """Starts `ollama serve` from inside DUO and cleans it up.

    Only ever manages a server DUO itself started: the process handle
    is tracked here, and stop() terminates just that process. A server
    the user started themselves is never touched.

    start() is blocking (it waits for the probe, up to ``timeout``
    seconds) -- call it off the GUI thread. It returns (ok, message):
    ok is True with "Ollama is running." on success, False with a
    plain-language reason otherwise.
    """

    START_TIMEOUT = 15.0
    PROBE_INTERVAL = 0.5

    def __init__(self, client):
        self.client = client
        self._process = None
        self._lock = threading.Lock()

    @property
    def running(self):
        """True while a DUO-started server process is alive."""
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self, timeout=None):
        """Start Ollama if it is not already answering.

        Already running (by anyone) counts as success -- nothing is
        spawned. Returns (ok, message); never raises.
        """
        if timeout is None:
            timeout = self.START_TIMEOUT
        try:
            if self.client.probe():
                return True, "Ollama is running."
        except Exception:
            pass
        if shutil.which("ollama") is None:
            return False, MSG_OLLAMA_NOT_INSTALLED
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            # A previous start is still coming up: wait on it instead
            # of spawning a second server and orphaning the first.
            pass
        else:
            try:
                process = subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL, start_new_session=True)
            except OSError:
                return False, MSG_OLLAMA_START_FAILED
            with self._lock:
                self._process = process
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.client.probe(timeout=2):
                    return True, "Ollama is running."
            except Exception:
                pass
            if process.poll() is not None:
                # The server died on its own (already running under
                # another user, bad install, ...): one last probe in
                # case another server is actually answering.
                try:
                    if self.client.probe(timeout=2):
                        return True, "Ollama is running."
                except Exception:
                    pass
                with self._lock:
                    self._process = None
                return False, MSG_OLLAMA_START_FAILED
            time.sleep(self.PROBE_INTERVAL)
        return False, MSG_OLLAMA_START_FAILED

    def stop(self):
        """Stop the server DUO started, if it is still running.

        Terminates only DUO's own process; a server started any other
        way is left alone. Never raises."""
        with self._lock:
            process = self._process
            self._process = None
        if process is None:
            return
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        except Exception:
            pass
