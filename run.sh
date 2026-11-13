#!/bin/bash
# DUO - start the app.
# Run with: bash run.sh
cd "$(dirname "$0")"
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
