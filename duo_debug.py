"""Debug log layer for DUO: the BNB T21 standard, vendored as
debug_log.py next to this file.

Moved verbatim from dual_chat.py in v0.42 (redesign U20): the window
file was 8 lines under its 4,000-line bound, and this block -- the
vendored-module import, the null-logger fallback, and the factory --
is self-contained. Logging must never break the app, so a missing
module falls back to a do-nothing logger instead of crashing the
import.

APP_NAME and APP_VERSION live in dual_chat (the single source) and
are imported lazily inside the two methods that need them, the same
way duo_deck.py does -- this module stays importable standalone.
"""

import os

# Debug log layer: the BNB T21 standard, vendored as debug_log.py next
# to this file. Logging must never break the app, so a missing module
# falls back to a do-nothing logger instead of crashing the import.
try:
    import debug_log as _debug_log
except ImportError:  # pragma: no cover - the zip always ships it
    _debug_log = None


class _NullLogger:
    """Drop-in for DebugLogger when the module is missing: every call
    is a no-op, and a report request reports that logging is off."""

    def debug(self, *a, **k):
        pass

    def info(self, *a, **k):
        pass

    def warning(self, *a, **k):
        pass

    def error(self, *a, **k):
        pass

    def exception(self, *a, **k):
        pass

    def action(self, *a, **k):
        pass

    def set_setting(self, *a, **k):
        pass

    def register_service(self, *a, **k):
        pass

    def register_dependency(self, *a, **k):
        pass

    def build_report(self):
        # APP_NAME/APP_VERSION live in dual_chat (the single source);
        # imported lazily so this module stays importable standalone.
        from dual_chat import APP_NAME, APP_VERSION
        return "DEBUG REPORT — %s %s\n\nLogging is not available.\n" % (
            APP_NAME, APP_VERSION)

    def generate_report(self, path=None):
        if path is None:
            path = "debug-report-unavailable.txt"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(self.build_report())
        return path

    def install_crash_handler(self):
        pass

    def uninstall_crash_handler(self):
        pass


def make_debug_logger(log_dir=None):
    """A DebugLogger for this app, or a null logger when the vendored
    module is missing. The log file lives next to the app
    (duo-debug.log), rotating so it never grows without bound."""
    if _debug_log is None:
        return _NullLogger()
    # APP_VERSION lives in dual_chat (the single source); imported
    # lazily so this module stays importable standalone.
    from dual_chat import APP_VERSION
    if log_dir is None:
        log_dir = os.path.dirname(os.path.abspath(__file__))
    try:
        return _debug_log.DebugLogger("duo-debug", APP_VERSION,
                                      log_dir=log_dir)
    except OSError:
        return _NullLogger()
