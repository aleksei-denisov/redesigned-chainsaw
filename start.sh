#!/usr/bin/env bash
# One-step launcher for macOS (and Linux):
#   creates .venv on first run, installs requirements, starts the simulator.
# Usage:  ./start.sh            (extra arguments are passed to main.py)
set -euo pipefail
cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON:-python3}"
if [ ! -x ".venv/bin/python" ]; then
  echo "Creating virtual environment in .venv ..."
  "$PYTHON_BIN" -m venv .venv
fi

if ! .venv/bin/python -c "import numpy, scipy, PySide6, pyqtgraph" >/dev/null 2>&1; then
  echo "Installing dependencies ..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

exec .venv/bin/python main.py "$@"
