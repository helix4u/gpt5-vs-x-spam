#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
DATA="data"
rm -f "$DATA/dataset.jsonl" "$DATA/results.jsonl" || true
if [[ -d "$DATA/cache" ]]; then
  rm -f "$DATA/cache"/* 2>/dev/null || true
fi
echo "Cleared dataset, results, and cache in $(pwd)/$DATA."

