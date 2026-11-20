#!/bin/bash
# DUO - start the app.
# Run with: bash run.sh
#
# Since v0.59 DUO runs on Fyrn, the engine installed on this PC:
# when a Fyrn install is found, DUO restarts itself under Fyrn's
# venv python (which has the engine and its Qt), with this folder
# on PYTHONPATH and FYRN_INSTALL_DIR naming the install -- the
# Fyrn Chat Test launcher pattern. When Fyrn is not installed,
# DUO starts under its own venv python instead, so the window
# still opens and says in plain words that Fyrn is missing.
cd "$(dirname "$0")"
APP_DIR="$(pwd)"

# A Fyrn install is a folder with install.sh, the fyrn package,
# and a venv. Prints the folder, or nothing. The search order
# matches duo_fyrn.find_fyrn_install: the FYRN_INSTALL_DIR
# override, then the ~/.local/bin/fyrn launcher's APP_DIR line,
# then a search of the home folder.
find_fyrn() {
  local dir=""
  if [ -n "${FYRN_INSTALL_DIR:-}" ]; then
    dir="$FYRN_INSTALL_DIR"
    if [ -f "$dir/install.sh" ] \
        && [ -f "$dir/fyrn/__init__.py" ] \
        && [ -x "$dir/.venv/bin/python" ]; then
      printf '%s' "$dir"
      return 0
    fi
    # A set-but-wrong override falls through to the other two,
    # like the stale-launcher case below; duo_fyrn reports the
    # override problem itself when it looks.
  fi
  if [ -f "$HOME/.local/bin/fyrn" ]; then
    dir="$(sed -n 's/^APP_DIR="\(.*\)"$/\1/p' \
      "$HOME/.local/bin/fyrn" | head -1)"
    if [ -f "$dir/install.sh" ] \
        && [ -f "$dir/fyrn/__init__.py" ] \
        && [ -x "$dir/.venv/bin/python" ]; then
      printf '%s' "$dir"
      return 0
    fi
  fi
  local found=""
  local count=0
  while IFS= read -r -d '' init; do
    dir="$(dirname "$(dirname "$init")")"
    if [ -f "$dir/install.sh" ] \
        && [ -x "$dir/.venv/bin/python" ]; then
      found="$dir"
      count=$((count + 1))
    fi
  done < <(find "$HOME" -maxdepth 6 -type d -name '.*' -prune -o \
    -type f -path '*/fyrn/__init__.py' -print0 2>/dev/null)
  if [ "$count" -eq 1 ]; then
    printf '%s' "$found"
    return 0
  fi
  if [ "$count" -gt 1 ]; then
    echo "MORE-THAN-ONE"
    return 0
  fi
  return 1
}

FYRN_DIR="$(find_fyrn)" || FYRN_DIR=""
if [ -n "$FYRN_DIR" ] && [ "$FYRN_DIR" != "MORE-THAN-ONE" ]; then
  # Run under Fyrn's venv: it has the engine (llama-cpp-python)
  # and PySide6. PYTHONPATH points at this folder so dual_chat
  # and the duo_* modules import from here; FYRN_INSTALL_DIR
  # tells duo_fyrn which Fyrn this is.
  PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    FYRN_INSTALL_DIR="$FYRN_DIR" \
    exec "$FYRN_DIR/.venv/bin/python" "$APP_DIR/dual_chat.py" "$@"
fi

# No usable Fyrn (not installed, or more than one): start under
# DUO's own venv so the window opens on the honest state --
# duo_fyrn says Fyrn is missing, or that more than one was
# found, in plain words.
if [ ! -x .venv/bin/python ]; then
  echo "DUO isn't installed yet -- the app folder (.venv) is missing."
  echo "Run the installer first:"
  echo ""
  echo "    bash install.sh"
  echo ""
  echo "Then start DUO again with: bash run.sh"
  exit 1
fi
exec .venv/bin/python dual_chat.py "$@"
