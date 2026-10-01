#!/bin/bash
set -euo pipefail

BASE="/usr/local/share/skylight-patcher"
exec /usr/bin/python3 "$BASE/scripts/patcher.py" \
  --patch-dir "$BASE/patches" \
  --watch \
  --interval 2
