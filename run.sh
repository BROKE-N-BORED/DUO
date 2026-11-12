#!/bin/bash
# Dual Chat - start the app.
# Run with: bash run.sh
cd "$(dirname "$0")"
if [ -x .venv/bin/python ]; then
  exec .venv/bin/python dual_chat.py "$@"
fi
exec python3 dual_chat.py "$@"
