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

# Pinned: this is the version the app was built and verified against.
echo "Installing PySide6-Essentials 6.12.0 (one time, may take a minute)..."
.venv/bin/python -m pip install --quiet "PySide6-Essentials==6.12.0"

# Post-install check (fatal): the package must be present before the
# install is called a success. importlib.metadata only -- no native
# libraries are loaded here, so this check cannot crash on a missing
# system Qt library.
echo "Checking the install..."
if ! .venv/bin/python -c "import importlib.metadata; importlib.metadata.version('PySide6-Essentials')"; then
  echo "The install did not finish: PySide6-Essentials is missing."
  echo "Try running: bash install.sh"
  exit 1
fi

# The package is present, but the window still needs the machine's Qt
# system libraries. Start Qt once (offscreen -- no window appears):
# if a library is missing, warn plainly and name it, instead of
# letting the first launch crash. (A warning, not a failure -- the
# install itself is done.)
if ! QT_CHECK_ERR=$(QT_QPA_PLATFORM=offscreen .venv/bin/python -c "from PySide6 import QtWidgets; QtWidgets.QApplication([])" 2>&1); then
  echo ""
  echo "Warning: DUO is installed, but its window may not open on"
  echo "this machine -- Qt could not start."
  MISSING_LIB=$(echo "$QT_CHECK_ERR" | grep -o "lib[A-Za-z0-9_.-]*\.so[^ :]*" | head -n 1 || true)
  if [ -n "$MISSING_LIB" ]; then
    echo "The missing library is: $MISSING_LIB"
  else
    echo "The check said:"
    echo "$QT_CHECK_ERR" | tail -n 1
  fi
  echo "You may need your distribution's Qt libraries (on Linux Mint:"
  echo "  sudo apt install libegl1 libgl1"
  echo ")"
  echo ""
  echo "Installed anyway. Start it with: bash run.sh"
  exit 0
fi

echo ""
echo "Done. Start it with: bash run.sh"
