"""Background Qt workers for DUO (moved verbatim from dual_chat.py
in v0.39, redesign R5 -- dual_chat.py was 17 lines under its 4,000-line
bound and the first-run guide needed room).

The signal bridge (_Signals / _SignalObject) carries runner callbacks
from worker threads onto the GUI thread, and the three thread classes
load the model list, start Ollama, and run the update check off the
GUI thread. PySide6 is imported lazily inside the functions (the
duo_menu.py precedent), so this module -- and dual_chat.py, which
imports it -- still import cleanly on a machine without PySide6
(the v0.14 first-run guard).
"""
import time

from duo_ollama import OllamaError, check_for_update


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
            ollama_started = Signal(bool, str)
            # Update check: (result, manual) where result is
            # check_for_update's ("newer"/"latest"/"failed", version)
            # and manual says whether the button asked for it.
            update_checked = Signal(object, bool)
            # Phone deck send: (text, target, holder dict with a
            # "done" Event and an "error" slot the GUI thread fills).
            phone_send = Signal(str, str, object)

        self._inner = _Obj()
        self.started = self._inner.started
        self.chunk = self._inner.chunk
        self.done = self._inner.done
        self.failed = self._inner.failed
        self.cancelled = self._inner.cancelled
        self.idle = self._inner.idle
        self.models_loaded = self._inner.models_loaded
        self.models_failed = self._inner.models_failed
        self.ollama_started = self._inner.ollama_started
        self.update_checked = self._inner.update_checked
        self.phone_send = self._inner.phone_send


class _ModelLoader:
    """Loads the model list off the GUI thread.

    on_result (optional) is called in the loader thread with
    (ok, models_or_message, seconds) -- the debug log uses it to
    record the probe outcome and its latency.
    """

    def __init__(self, client, signals, on_result=None):
        from PySide6.QtCore import QThread

        class _Thread(QThread):
            def run(self):
                started = time.monotonic()
                try:
                    # Sizes ride along with the names (v0.33): the
                    # picker shows each model's size and whether it
                    # fits this PC. A client without the new method
                    # (an old test stub) falls back to bare names
                    # with no sizes.
                    with_sizes = getattr(
                        client, "list_models_with_sizes", None)
                    if with_sizes is not None:
                        models = with_sizes()
                    else:
                        models = [(name, None) for name in
                                  client.list_models()]
                    if on_result is not None:
                        on_result(True, models,
                                  time.monotonic() - started)
                    try:
                        signals.models_loaded.emit(models)
                    except RuntimeError:
                        pass  # Window gone; nothing left to update.
                except OllamaError as exc:
                    if on_result is not None:
                        on_result(False, str(exc),
                                  time.monotonic() - started)
                    try:
                        signals.models_failed.emit(str(exc))
                    except RuntimeError:
                        pass  # Window gone; nothing left to update.

        self.thread = _Thread()


class _OllamaStarter:
    """Starts Ollama off the GUI thread (the wait for the probe can
    take seconds). Emits (ok, message) through signals.ollama_started."""

    def __init__(self, manager, signals):
        from PySide6.QtCore import QThread

        class _Thread(QThread):
            def run(self):
                ok, message = manager.start()
                try:
                    signals.ollama_started.emit(ok, message)
                except RuntimeError:
                    pass  # Window gone; nothing left to update.

        self.thread = _Thread()


class _UpdateChecker:
    """Runs the update check off the GUI thread (the network call
    can take up to the 5s timeout). Emits (result, manual) through
    signals.update_checked, where result is check_for_update's
    ("newer"/"latest"/"failed", version) tuple.

    check_fn (optional) is the check to run: dual_chat.py passes
    its own check_for_update name, so tests can stub the network
    by patching dual_chat.check_for_update -- the seam the v0.28
    tests were written against, kept alive the same way the v0.25
    split kept dual_chat.shutil for its patch point. When omitted
    the module's own check_for_update runs."""

    def __init__(self, signals, current_version, manual,
                 check_fn=None):
        from PySide6.QtCore import QThread

        class _Thread(QThread):
            def run(self):
                fn = (check_fn if check_fn is not None
                      else check_for_update)
                result = fn(current_version)
                try:
                    signals.update_checked.emit(result, manual)
                except RuntimeError:
                    pass  # Window gone; nothing left to update.

        self.thread = _Thread()
