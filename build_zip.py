#!/usr/bin/env python3
"""Build the DUO zip with correct stored file modes.

GUI extractors drop the execute bit unless external_attr carries the
regular-file type bits: 0o100755 for scripts, 0o100644 for plain files.
"""
import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(ROOT), "duo-v0.3.zip")

FILES = {
    "dual_chat.py": 0o100644,
    "tests/test_dual_chat.py": 0o100644,
    "install.sh": 0o100755,
    "run.sh": 0o100755,
    "README.md": 0o100644,
    "build_zip.py": 0o100644,
}


def main():
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, mode in FILES.items():
            path = os.path.join(ROOT, rel)
            info = zipfile.ZipInfo("duo/" + rel)
            info.external_attr = mode << 16
            with open(path, "rb") as fh:
                zf.writestr(info, fh.read())
    print("wrote %s (%d bytes)" % (OUT, os.path.getsize(OUT)))
    with zipfile.ZipFile(OUT) as zf:
        for info in zf.infolist():
            print("%s %s" % (oct(info.external_attr >> 16), info.filename))


if __name__ == "__main__":
    main()
