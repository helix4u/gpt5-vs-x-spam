#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if [[ ! -f .venv/Scripts/python.exe && ! -f .venv/bin/python ]]; then
  echo "venv missing. run install.bat or create backend/.venv first." >&2
  exit 1
fi
if [[ -f .venv/Scripts/python.exe ]]; then
  .venv/Scripts/python.exe -m playwright install
else
  .venv/bin/python -m playwright install
fi
echo "Playwright installation finished."
