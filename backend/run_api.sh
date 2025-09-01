#!/usr/bin/env bash
set -e
# change to this script's directory (backend)
cd "$(dirname "$0")"
python -m playwright install
uvicorn app.api:app --reload --port 8000
