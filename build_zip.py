#!/usr/bin/env python3
"""Build the DUO zip with correct stored file modes.

GUI extractors drop the execute bit unless external_attr carries the
regular-file type bits: 0o100755 for scripts, 0o100644 for plain files.
"""
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))


def app_version():
    """The version the zip is named for, read from dual_chat.py's
    APP_VERSION (v0.60, P1-26: the output name used to be a
    hardcoded string hand-edited per release -- one release
    forgetting the edit would ship a misnamed zip)."""
    with open(os.path.join(ROOT, "dual_chat.py"),
              encoding="utf-8") as fh:
        match = re.search(
            r'^APP_VERSION = "([^"]+)"', fh.read(), re.MULTILINE)
    if match is None:
        raise SystemExit("APP_VERSION not found in dual_chat.py")
    return match.group(1)


def out_path():
    return os.path.join(
        os.path.dirname(ROOT), "duo-v%s.zip" % app_version())

FILES = {
    "dual_chat.py": 0o100644,
    "duo_accessibility.py": 0o100644,
    "duo_attachments.py": 0o100644,
    "duo_constants.py": 0o100644,
    "duo_debug.py": 0o100644,
    "duo_fyrn.py": 0o100644,
    "duo_stats.py": 0o100644,
    "duo_text.py": 0o100644,
    "duo_tasks.py": 0o100644,
    "duo_markdown.py": 0o100644,
    "duo_model.py": 0o100644,
    "duo_menu.py": 0o100644,
    "duo_motion.py": 0o100644,
    "duo_deck.py": 0o100644,
    "duo_transcript.py": 0o100644,
    "duo_updater.py": 0o100644,
    "duo_pairs.py": 0o100644,
    "duo_storage.py": 0o100644,
    "duo_runner.py": 0o100644,
    "duo_single.py": 0o100644,
    "duo_sparkline.py": 0o100644,
    "duo_icon.py": 0o100644,
    "duo_workers.py": 0o100644,
    "debug_log.py": 0o100644,
    "qrcodegen.py": 0o100644,
    "tests/test_dual_chat.py": 0o100644,
    "install.sh": 0o100755,
    "run.sh": 0o100755,
    "uninstall.sh": 0o100755,
    "README.md": 0o100644,
    "CHANGELOG.md": 0o100644,
    "LICENSE": 0o100644,
    ".gitignore": 0o100644,
    ".github/workflows/ci.yml": 0o100644,
    "DUO.desktop": 0o100644,
    "Install DUO.desktop": 0o100755,
    "build_zip.py": 0o100644,
}


def check_complete():
    """Every app module in the folder must be in FILES, and every
    FILES entry must exist (v0.60, P1-26). A new duo_*.py module
    forgotten here would ship a zip that crashes on import, with
    nothing to catch it; this check is that catch."""
    problems = []
    for name in sorted(os.listdir(ROOT)):
        if not name.endswith(".py"):
            continue
        if (name.startswith("duo_") or name == "dual_chat.py"
                or name in ("debug_log.py", "qrcodegen.py")):
            if name not in FILES:
                problems.append("%s is not in FILES" % name)
    for rel in FILES:
        if not os.path.isfile(os.path.join(ROOT, rel)):
            problems.append("%s is in FILES but missing" % rel)
    if problems:
        raise SystemExit(
            "build_zip: incomplete file list:\n  "
            + "\n  ".join(problems))


def main():
    check_complete()
    out = out_path()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, mode in FILES.items():
            path = os.path.join(ROOT, rel)
            info = zipfile.ZipInfo("duo/" + rel)
            info.external_attr = mode << 16
            with open(path, "rb") as fh:
                zf.writestr(info, fh.read())
    print("wrote %s (%d bytes)" % (out, os.path.getsize(out)))
    with zipfile.ZipFile(out) as zf:
        for info in zf.infolist():
            print("%s %s" % (oct(info.external_attr >> 16), info.filename))


if __name__ == "__main__":
    main()
