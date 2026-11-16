#!/bin/bash
# DUO - one-time install.
# Creates a private folder (.venv) with PySide6-Essentials, the only
# package the app needs, adds DUO to the app menu, and puts a DUO
# icon on the desktop. If python3-venv or the Qt system libraries
# are missing, it tries to install them first (sudo apt -- the
# installer runs in a terminal, so a password prompt works); if
# that cannot happen it says the command plainly instead.
# Double-click "Install DUO" in the app folder, or run with:
# bash install.sh
# Pass --pause to wait for Enter at the end (the desktop launcher
# does this, so the window stays open until you have read it).
set -euo pipefail
cd "$(dirname "$0")"

PAUSE_AT_END=0
if [ "${1:-}" = "--pause" ]; then
  PAUSE_AT_END=1
fi

# When launched from the desktop, the terminal would close the
# moment the script ends -- wait for Enter first, so the result
# can be read. From a plain terminal run this does nothing.
pause_before_close() {
  if [ "$PAUSE_AT_END" = 1 ]; then
    printf "Press Enter to close this window. "
    read -r _dummy || true
  fi
}

# A visible confirmation when a desktop session is present; the
# terminal message below is the fallback and always prints.
confirm_installed() {
  if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    if command -v zenity >/dev/null 2>&1; then
      zenity --info --title="DUO" --text="DUO is installed. Double-click the DUO icon on the desktop to run it." 2>/dev/null || true
      return 0
    fi
    if command -v notify-send >/dev/null 2>&1; then
      notify-send "DUO" "DUO is installed. Double-click the DUO icon on the desktop to run it." 2>/dev/null || true
    fi
  fi
}

# The user's Desktop folder, per xdg-user-dir when it exists.
desktop_dir() {
  dir=""
  if command -v xdg-user-dir >/dev/null 2>&1; then
    dir=$(xdg-user-dir DESKTOP 2>/dev/null || true)
  fi
  if [ -n "$dir" ]; then
    printf '%s' "$dir"
  else
    printf '%s' "$HOME/Desktop"
  fi
}

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is missing. Install it first."
  pause_before_close
  exit 1
fi

# Python 3.10 or newer.
if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"; then
  echo "DUO needs Python 3.10 or newer."
  python3 --version
  pause_before_close
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating the app folder (.venv)..."
  if ! python3 -m venv .venv; then
    rm -rf .venv
    # python3-venv is missing. Try installing it first -- the
    # installer runs in a terminal, so a sudo password prompt
    # works here. If that cannot happen, say the command plainly.
    echo "python3-venv is missing. Trying to install it..."
    if command -v sudo >/dev/null 2>&1 && command -v apt >/dev/null 2>&1 \
        && sudo apt install -y python3-venv; then
      echo "Installed python3-venv. Trying again..."
      if ! python3 -m venv .venv; then
        rm -rf .venv
        echo "Still could not create the app folder."
        echo "Run: sudo apt install python3-venv"
        echo "Then run: bash install.sh"
        pause_before_close
        exit 1
      fi
    else
      echo "Could not install python3-venv automatically."
      echo "Run: sudo apt install python3-venv"
      echo "Then run: bash install.sh"
      pause_before_close
      exit 1
    fi
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
  pause_before_close
  exit 1
fi

# Draw the menu/desktop icon once (best effort): the app's own
# window icon, rendered offscreen. ICON_OK says whether duo.png
# exists afterwards; without it the entries are still written,
# just without an icon.
ICON_OK=0
draw_desktop_icon() {
  if QT_QPA_PLATFORM=offscreen .venv/bin/python -c "
import sys
from PySide6 import QtWidgets
from dual_chat import make_app_icon
app = QtWidgets.QApplication([])
pixmap = make_app_icon().pixmap(64, 64)
sys.exit(0 if not pixmap.isNull() and pixmap.save('duo.png') else 1)
" 2>/dev/null; then
    ICON_OK=1
  fi
}

# Write one installed .desktop file: the template with the real app
# folder baked in, executable.
write_desktop_file() {
  APP_DIR_ESC=$(printf '%s' "$PWD" | sed 's/[|&]/\\&/g')
  if [ "$ICON_OK" = 1 ]; then
    sed "s|@APP_DIR@|$APP_DIR_ESC|g" DUO.desktop \
      > "$1" || return 1
  else
    sed "s|@APP_DIR@|$APP_DIR_ESC|g; /^Icon=/d" DUO.desktop \
      > "$1" || return 1
  fi
  chmod +x "$1" 2>/dev/null || true
}

# Menu entry (best effort -- never fails the install): put DUO in the
# app menu, with an icon drawn from the app's own window icon. If the
# icon cannot be drawn on this machine, the menu entry still appears,
# just without an icon.
install_desktop_entry() {
  [ -n "${HOME:-}" ] || return 0
  [ -f DUO.desktop ] || return 0
  # Clone installs carry no execute bits -- make the launchers
  # runnable so the menu entry works from a clone too.
  chmod +x run.sh uninstall.sh 2>/dev/null || true
  DESKTOP_DIR="$HOME/.local/share/applications"
  mkdir -p "$DESKTOP_DIR" || return 1
  write_desktop_file "$DESKTOP_DIR/DUO.desktop" || return 1
  if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
  fi
  echo "Added DUO to the app menu."
}

# Desktop icon (best effort -- never fails the install): the same
# entry on the desktop, so DUO is one double-click away. A desktop
# icon also needs to be trusted before it launches: the exec bit
# (set above) plus gio's trusted flag. Check the flag actually
# took -- if gio is missing or the metadata store is not running,
# say plainly how to allow it by hand.
install_desktop_icon() {
  [ -n "${HOME:-}" ] || return 0
  [ -f DUO.desktop ] || return 0
  DESKTOP_DIR=$(desktop_dir) || return 1
  mkdir -p "$DESKTOP_DIR" || return 1
  write_desktop_file "$DESKTOP_DIR/DUO.desktop" || return 1
  TRUSTED=0
  if command -v gio >/dev/null 2>&1; then
    gio set "$DESKTOP_DIR/DUO.desktop" metadata::trusted true 2>/dev/null || true
    if gio info "$DESKTOP_DIR/DUO.desktop" 2>/dev/null | grep -q "metadata::trusted: true"; then
      TRUSTED=1
    fi
  fi
  echo "Added the DUO icon to the desktop."
  if [ "$TRUSTED" = 0 ]; then
    echo "If double-clicking the desktop icon does nothing, right-click"
    echo "the icon and choose Allow Launching."
  fi
}

draw_desktop_icon
install_desktop_entry || echo "Note: could not add the menu entry; DUO still runs with: bash run.sh"
install_desktop_icon || echo "Note: could not add the desktop icon; DUO is still in the app menu, or run it with: bash run.sh"

# The package is present, but the window still needs the machine's Qt
# system libraries. Start Qt once (offscreen -- no window appears):
# if a library is missing, try installing the libraries first, and
# only warn plainly (naming the missing one) if that cannot happen.
# (A warning, not a failure -- the install itself is done.)
qt_starts() {
  QT_QPA_PLATFORM=offscreen .venv/bin/python -c "from PySide6 import QtWidgets; QtWidgets.QApplication([])" 2>/dev/null
}

if ! QT_CHECK_ERR=$(QT_QPA_PLATFORM=offscreen .venv/bin/python -c "from PySide6 import QtWidgets; QtWidgets.QApplication([])" 2>&1); then
  echo ""
  echo "DUO is installed, but its window may not open on this"
  echo "machine -- Qt could not start. Trying to install the"
  echo "missing libraries..."
  QT_FIXED=0
  if command -v sudo >/dev/null 2>&1 && command -v apt >/dev/null 2>&1 \
      && sudo apt install -y libegl1 libgl1 && qt_starts; then
    QT_FIXED=1
  fi
  if [ "$QT_FIXED" = 0 ]; then
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
    echo "Installed anyway. Double-click the DUO icon on the desktop"
    echo "to run it (or start it with: bash run.sh)."
    pause_before_close
    exit 0
  fi
  echo "Installed the missing libraries. Qt starts now."
fi

echo ""
echo "Done. DUO is installed."
echo "Double-click the DUO icon on the desktop to run it."
echo "It is also in the app menu."
confirm_installed
pause_before_close
