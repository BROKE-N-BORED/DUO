"""SerialRunner: generation jobs, strictly one at a time, in
submit order. Threaded; the GUI passes Qt-signal callbacks in.

Split out of dual_chat.py in v0.25 (audit T14).
"""

import queue
import threading
import traceback

from duo_fyrn import MSG_TURN_FAILED, Cancelled, EngineError


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

    def _safe(self, callback, *args):
        """Invoke one GUI callback, logging and swallowing any
        exception it raises (v0.60, P1-15).

        This loop is the only runner thread: if a callback raised
        out of it -- on_error inside an except block, on_cancelled,
        on_idle -- the thread died, the queue never drained again,
        and the window sat "busy" forever with no log and no
        recovery. A broken callback is a GUI bug; it is logged
        here and the queue keeps moving.
        """
        try:
            callback(*args)
        except Exception:
            traceback.print_exc()

    def _loop(self):
        while True:
            job = self._queue.get()
            job_id = job["id"]
            with self._cancel_lock:
                was_cancelled = job_id in self._cancelled
                if was_cancelled:
                    self._cancelled.discard(job_id)
            if was_cancelled:
                self._safe(self.on_cancelled, job)
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
                    self._safe(self.on_start, job)
                    full = job["fn"](
                        lambda text: self._safe(
                            self.on_chunk, job, text), event)
                    self._safe(self.on_done, job, full)
                except Cancelled:
                    self._safe(self.on_cancelled, job)
                except EngineError as exc:
                    self._safe(self.on_error, job, str(exc))
                except Exception:
                    # v0.58 (P2-52): an unexpected failure is not a
                    # model load failure; the old catch-all blamed
                    # the model for every hiccup.
                    self._safe(self.on_error, job, MSG_TURN_FAILED)
                finally:
                    with self._cancel_lock:
                        self._current_id = None
                        self._current_event = None
                        # A cancel that landed while this job ran must
                        # not poison the next job with the same id: the
                        # entry was for THIS job, and no other job with
                        # this id can be queued while it runs (the GUI
                        # submits a side's next job only after the
                        # previous one finishes).
                        self._cancelled.discard(job_id)
            self._queue.task_done()
            if self._queue.empty():
                self._safe(self.on_idle)
