#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

if [[ ${EUID} -ne 0 ]]; then
  exec sudo /usr/bin/python3 "$ROOT/scripts/patcher.py" \
    --patch-dir "$ROOT/patches" --once "$@"
fi

exec /usr/bin/python3 "$ROOT/scripts/patcher.py" \
  --patch-dir "$ROOT/patches" --once "$@"
