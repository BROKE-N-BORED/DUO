#!/bin/bash
# DUO - uninstall.
# Removes the menu entry, the app folder (.venv), and the files DUO
# created next to the app (saved pairs, debug log, menu icon).
# Asks first. Nothing outside the app folder is touched except the
# menu entry the installer put in ~/.local/share/applications/.
# Run with: bash uninstall.sh
set -euo pipefail
cd "$(dirname "$0")"

echo "This removes:"
echo "  - the DUO menu entry"
echo "  - the app folder (.venv)"
echo "  - saved pairs (duo-pairs.json), the debug log (duo-debug.log),"
echo "    and the menu icon (duo.png)"
echo "The app folder itself stays -- delete it yourself if you like."
printf "Remove DUO? [y/N] "
read -r answer || answer=""
case "$answer" in
  y|Y|yes|YES) ;;
  *) echo "Nothing removed."; exit 0 ;;
esac

REMOVED=""
if [ -n "${HOME:-}" ] && [ -f "$HOME/.local/share/applications/DUO.desktop" ]; then
  rm -f "$HOME/.local/share/applications/DUO.desktop"
  REMOVED="$REMOVED
  - menu entry"
fi
for f in .venv duo-pairs.json duo-debug.log duo.png; do
  if [ -e "$f" ]; then
    rm -rf "$f"
    REMOVED="$REMOVED
  - $f"
  fi
done
if [ -n "$REMOVED" ]; then
  echo "Removed:$REMOVED"
else
  echo "Nothing to remove -- DUO was not installed here."
fi
echo "Done. The app folder itself is still here; delete it if you like."
