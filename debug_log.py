#!/usr/bin/env python3
"""debug_log — drop-in debug log layer for BNB projects.

One module, standard library only. Any project drops this file in,
creates a DebugLogger at startup, and gets:

- timestamped logging to a rotating file (capped size, cheap on disk)
- a ring buffer of the last N actions/steps before an error
- error capture with full tracebacks
- a context snapshot (app, OS, Python, RAM/disk, dependency versions,
  scrubbed settings, service status)
- a debug report, on demand or automatically on a crash, in the
  standard format — one file, safe to email
- secret scrubbing: API keys, tokens, passwords, and auth headers are
  redacted from every log line and every report. A report must be safe
  to send to broke.n.bored.studio@gmail.com.

Pairs with BNB SKILLS debug-log-layer (S61), which is the standard
this module implements.

Usage (library):

    from debug_log import DebugLogger, check_ollama

    log = DebugLogger("duo", "0.3.1", log_dir="logs")
    log.register_service("ollama", check_ollama)
    log.set_setting("model", "qwen3.5:2b")
    log.install_crash_handler()
    log.action("user pressed Send to Both")
    log.info("relay started")
    ...
    path = log.generate_report()   # on demand, e.g. from a button

Usage (one command, from the project folder):

    python3 debug_log.py report --app duo --version 0.3.1
"""

import argparse
import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import sys
import tempfile
import traceback
from collections import deque
from datetime import datetime

try:
    from importlib import metadata as importlib_metadata
except ImportError:  # Python < 3.8, not expected on his PC
    importlib_metadata = None

# ---------------------------------------------------------------- scrubbing

REDACTED = "[REDACTED]"

# Keys whose values are secrets. Any setting/log-field key containing
# one of these fragments (case-insensitive) is redacted wholesale.
SENSITIVE_KEY_FRAGMENTS = (
    "key", "token", "secret", "password", "passwd", "pwd",
    "auth", "credential", "private",
)

_SCRUB_PATTERNS = [
    # Authorization: Bearer <value> / Basic <value>
    (re.compile(r"(Authorization:\s*(?:Bearer|Basic)\s+)\S+",
                re.IGNORECASE), r"\1" + REDACTED),
    # api_key=... / password: ... / secret = "..." (quoted or bare)
    (re.compile(r"(?i)(api[_-]?key|api[_-]?token|access[_-]?token|"
                r"auth[_-]?token|secret|password|passwd|pwd)"
                r"(\s*[:=]\s*)[\"']?\S+?[\"']?(?=[\s,}\]]|$)"),
     r"\1\2" + REDACTED),
    # token=... in a URL query string
    (re.compile(r"(?i)([?&]token=)[^&\s]+"), r"\1" + REDACTED),
    # sk-... style API keys
    (re.compile(r"sk-[A-Za-z0-9_\-]{16,}"), REDACTED),
    # GitHub personal access tokens
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), REDACTED),
    # user:password@ in a URL
    (re.compile(r"(https?://)([^:/\s]+):([^@\s]+)@"),
     r"\1" + REDACTED + "@"),
]


def is_sensitive_key(name):
    """True when a key name marks its value as a secret."""
    lowered = str(name).lower()
    return any(frag in lowered for frag in SENSITIVE_KEY_FRAGMENTS)


def scrub_text(text):
    """Redact secret values from a log line or report fragment."""
    if not isinstance(text, str):
        text = str(text)
    for pattern, replacement in _SCRUB_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def scrub_value(value):
    """Redact one setting value, keeping only present/absent."""
    if value is None or value == "":
        return "[absent]"
    return REDACTED + " — present"


def scrub_dict(data):
    """Recursively scrub a settings dict.

    Sensitive keys keep their name and gain a present/absent marker;
    other string values pass through scrub_text so a secret pasted
    into a free-form value is still caught.
    """
    if isinstance(data, dict):
        out = {}
        for key, value in data.items():
            if is_sensitive_key(key):
                out[key] = scrub_value(value)
            else:
                out[key] = scrub_dict(value)
        return out
    if isinstance(data, (list, tuple)):
        return [scrub_dict(item) for item in data]
    if isinstance(data, str):
        return scrub_text(data)
    return data


# ---------------------------------------------------------------- context


def _read_meminfo():
    """RAM figures from /proc/meminfo (Linux); None elsewhere."""
    try:
        total = available = None
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1]) // 1024
                elif line.startswith("MemAvailable:"):
                    available = int(line.split()[1]) // 1024
                if total is not None and available is not None:
                    break
        if total is None:
            return None
        return {"total_mb": total, "available_mb": available}
    except (OSError, ValueError):
        return None


def _disk_usage(path):
    try:
        usage = shutil.disk_usage(path)
        return {"total_gb": round(usage.total / 1e9, 1),
                "free_gb": round(usage.free / 1e9, 1)}
    except OSError:
        return None


def _package_version(name):
    if importlib_metadata is None:
        return "unknown"
    try:
        return importlib_metadata.version(name)
    except importlib_metadata.PackageNotFoundError:
        return "not installed"
    except Exception:  # never let a version lookup break the report
        return "unknown"


def check_ollama(base_url="http://127.0.0.1:11434", timeout=2):
    """Ollama status for the report: running + models, or not reachable.

    This is the check the DUO case needed — Ollama dying silently left
    nothing to diagnose from. A short timeout keeps the report fast.
    """
    import urllib.request
    import urllib.error
    try:
        with urllib.request.urlopen(base_url + "/api/tags",
                                    timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return "reachable but errored (HTTP %s)" % exc.code
    except Exception:
        return "not reachable at %s" % base_url
    models = [m.get("name", "?") for m in payload.get("models", [])]
    if models:
        return "running (%d model(s): %s)" % (len(models),
                                              ", ".join(models))
    return "running (no models loaded)"


def collect_context(app_name, version, settings=None, services=None,
                    dependencies=None, path="."):
    """Snapshot of everything a diagnosis needs. Never raises."""
    context = {
        "app": app_name,
        "version": version,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "os": platform.platform(),
        "python": platform.python_version(),
        "ram": _read_meminfo() or "unknown",
        "disk": _disk_usage(path) or "unknown",
    }
    if dependencies:
        context["dependencies"] = {
            name: _package_version(name) for name in dependencies
        }
    if settings is not None:
        context["settings"] = scrub_dict(settings)
    if services:
        checked = {}
        for name, check in services.items():
            try:
                checked[name] = str(check())
            except Exception as exc:  # a failing check is a finding
                checked[name] = "check failed: %s" % exc
        context["services"] = checked
    return context


# ---------------------------------------------------------------- logger


class _RingBufferHandler(logging.Handler):
    """Keeps the last N formatted log records in memory."""

    def __init__(self, size):
        super().__init__()
        self.records = deque(maxlen=size)

    def emit(self, record):
        try:
            self.records.append(self.format(record))
        except Exception:
            self.handleError(record)


class _ErrorCaptureHandler(logging.Handler):
    """Keeps the last few ERROR+ records with their tracebacks."""

    def __init__(self, size=20):
        super().__init__()
        self.setLevel(logging.ERROR)
        self.errors = deque(maxlen=size)

    def emit(self, record):
        try:
            self.errors.append(self.format(record))
        except Exception:
            self.handleError(record)


class DebugLogger:
    """One logger + report generator per app.

    log_dir: where the rotating log lives (default "logs" under the
    project folder). max_bytes/backup_count cap the log's disk use
    (default 1 MB x 3 files). history: how many recent actions/steps
    the ring buffer keeps (default 200).
    """

    def __init__(self, app_name, version, log_dir="logs",
                 max_bytes=1_000_000, backup_count=3, history=200):
        self.app_name = app_name
        self.version = version
        self.log_dir = log_dir
        self.log_path = os.path.join(log_dir, app_name + ".log")
        self.settings = {}
        self.services = {}
        self.dependencies = []
        self._crash_installed = False
        self._previous_excepthook = None

        os.makedirs(log_dir, exist_ok=True)
        self.logger = logging.getLogger("debug_log." + app_name)
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S")
        file_handler = logging.handlers.RotatingFileHandler(
            self.log_path, maxBytes=max_bytes, backupCount=backup_count,
            encoding="utf-8")
        file_handler.setFormatter(fmt)
        self.logger.addHandler(file_handler)
        self._ring = _RingBufferHandler(history)
        self._ring.setFormatter(fmt)
        self.logger.addHandler(self._ring)
        self._errors = _ErrorCaptureHandler()
        self._errors.setFormatter(fmt)
        self.logger.addHandler(self._errors)

    # -- logging ------------------------------------------------------
    def debug(self, msg, *args):
        self.logger.debug(scrub_text(msg % args if args else msg))

    def info(self, msg, *args):
        self.logger.info(scrub_text(msg % args if args else msg))

    def warning(self, msg, *args):
        self.logger.warning(scrub_text(msg % args if args else msg))

    def error(self, msg, *args):
        self.logger.error(scrub_text(msg % args if args else msg))

    def exception(self, msg, *args):
        """Log an error with the current exception's full traceback."""
        self.logger.exception(scrub_text(msg % args if args else msg))

    def action(self, text):
        """Record one user/system step (goes to the ring buffer)."""
        self.logger.info("ACTION: %s", scrub_text(text))

    # -- configuration ------------------------------------------------
    def set_setting(self, key, value):
        """A setting that affects behaviour (scrubbed at report time)."""
        self.settings[key] = value

    def register_service(self, name, check):
        """A dependency to status-check in the report.

        check is a zero-arg callable returning a short status string.
        """
        self.services[name] = check

    def register_dependency(self, package_name):
        """A package whose installed version goes in the report."""
        if package_name not in self.dependencies:
            self.dependencies.append(package_name)

    # -- report -------------------------------------------------------
    def report_filename(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        return "debug-report-%s-%s.txt" % (self.app_name, stamp)

    def _log_tail(self, lines=200):
        try:
            with open(self.log_path, encoding="utf-8",
                      errors="replace") as fh:
                tail = deque(fh, maxlen=lines)
            return "".join(scrub_text(line) for line in tail)
        except OSError:
            return "(no log file yet)\n"

    def build_report(self):
        """The report text. Scrubbed throughout; safe to email."""
        context = collect_context(
            self.app_name, self.version, settings=self.settings,
            services=self.services, dependencies=self.dependencies,
            path=self.log_dir)
        sections = []
        sections.append("DEBUG REPORT — %s %s"
                        % (self.app_name, self.version))
        sections.append("Generated: %s" % context["generated"])
        sections.append("")
        sections.append("== Context ==")
        sections.append("OS: %s" % context["os"])
        sections.append("Python: %s" % context["python"])
        ram = context["ram"]
        if isinstance(ram, dict):
            sections.append("RAM: %s MB total, %s MB available"
                            % (ram["total_mb"], ram["available_mb"]))
        else:
            sections.append("RAM: %s" % ram)
        disk = context["disk"]
        if isinstance(disk, dict):
            sections.append("Disk: %s GB total, %s GB free"
                            % (disk["total_gb"], disk["free_gb"]))
        else:
            sections.append("Disk: %s" % disk)
        if "dependencies" in context:
            sections.append("Dependencies:")
            for name, ver in context["dependencies"].items():
                sections.append("  %s: %s" % (name, ver))
        sections.append("")
        sections.append("== Settings ==")
        settings = context.get("settings") or {}
        if settings:
            for key in sorted(settings):
                sections.append("  %s: %s" % (key, settings[key]))
        else:
            sections.append("  (none registered)")
        sections.append("")
        sections.append("== Services ==")
        services = context.get("services") or {}
        if services:
            for name in sorted(services):
                sections.append("  %s: %s"
                                % (name, scrub_text(services[name])))
        else:
            sections.append("  (none registered)")
        sections.append("")
        sections.append("== Recent actions (last %d) =="
                        % len(self._ring.records))
        if self._ring.records:
            sections.extend(self._ring.records)
        else:
            sections.append("  (none recorded)")
        sections.append("")
        sections.append("== Errors ==")
        if self._errors.errors:
            sections.extend(self._errors.errors)
        else:
            sections.append("  (no errors captured)")
        sections.append("")
        sections.append("== Log tail (last 200 lines, scrubbed) ==")
        sections.append(self._log_tail())
        return "\n".join(sections)

    def generate_report(self, path=None):
        """Write the report file; return its path.

        Default location is the log dir. Pass an explicit path (e.g.
        the project folder) when the user needs to find it easily.
        """
        if path is None:
            path = os.path.join(self.log_dir, self.report_filename())
        # Two reports in the same second must not overwrite each
        # other (a crash right after a manual report is the classic
        # case): suffix -2, -3, ... until the name is free.
        if os.path.exists(path):
            base, ext = os.path.splitext(path)
            n = 2
            while os.path.exists("%s-%d%s" % (base, n, ext)):
                n += 1
            path = "%s-%d%s" % (base, n, ext)
        text = self.build_report()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.info("debug report written to %s", path)
        return path

    # -- crash hook ---------------------------------------------------
    def install_crash_handler(self):
        """On an unhandled exception, write a report, then re-raise.

        Safe to call twice; restores cleanly via uninstall.
        """
        if self._crash_installed:
            return
        self._previous_excepthook = sys.excepthook
        logger = self

        def _hook(exc_type, exc_value, exc_tb):
            try:
                formatted = "".join(traceback.format_exception(
                    exc_type, exc_value, exc_tb))
                logger.error("UNHANDLED EXCEPTION:\n%s",
                             scrub_text(formatted))
                logger.generate_report()
            except Exception:
                pass  # the report must never hide the crash itself
            if logger._previous_excepthook is not None:
                logger._previous_excepthook(
                    exc_type, exc_value, exc_tb)

        sys.excepthook = _hook
        self._crash_installed = True

    def uninstall_crash_handler(self):
        if self._crash_installed:
            sys.excepthook = self._previous_excepthook
            self._crash_installed = False


# ---------------------------------------------------------------- CLI


def _cli_report(args):
    logger = DebugLogger(args.app, args.version, log_dir=args.log_dir)
    if args.ollama:
        logger.register_service("ollama", check_ollama)
    path = logger.generate_report(args.out)
    print("debug report written to %s" % path)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Debug log layer: generate a standard debug report.")
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser(
        "report", help="write a debug report file and print its path")
    report.add_argument("--app", required=True,
                        help="app name (goes in the report + filename)")
    report.add_argument("--version", default="unknown",
                        help="app version/build")
    report.add_argument("--log-dir", default="logs",
                        help="log directory (default: logs)")
    report.add_argument("--out", default=None,
                        help="report path (default: <log-dir>/"
                             "debug-report-<app>-<date>.txt)")
    report.add_argument("--ollama", action="store_true",
                        help="include an Ollama status check")
    args = parser.parse_args(argv)
    if args.command == "report":
        return _cli_report(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
