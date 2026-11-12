#!/bin/bash
# DUO - one-time install.
# Creates a private folder (.venv) with PySide6-Essentials, the only
# package the app needs. No sudo, nothing installed system-wide.
# Run with: bash install.sh
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is missing. Install it first."
  exit 1
fi

# Python 3.10 or newer.
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"; then
  echo "DUO needs Python 3.10 or newer."
  python3 --version
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating the app folder (.venv)..."
  if ! python3 -m venv .venv; then
    rm -rf .venv
    echo "python3-venv is missing. Run: sudo apt install python3-venv"
    echo "Then run: bash install.sh"
    exit 1
  fi
fi

echo "Installing PySide6-Essentials (one time, may take a minute)..."
.venv/bin/python -m pip install --quiet PySide6-Essentials
echo ""
echo "Done. Start it with: bash run.sh"
