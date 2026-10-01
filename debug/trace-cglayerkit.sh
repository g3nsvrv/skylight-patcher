#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
TRACE_SCRIPT="$ROOT_DIR/trace_os_log.py"

if [[ ! -f "$TRACE_SCRIPT" ]]; then
  echo "Missing: $TRACE_SCRIPT" >&2
  exit 1
fi

PID="$(pgrep -x WindowServer | head -n1 || true)"
if [[ -z "$PID" ]]; then
  echo "WindowServer is not running" >&2
  exit 1
fi

LLDB="$(xcrun -f lldb)"
echo "WindowServer PID: $PID"
echo "Waiting for exactly one matching CGLayerKit error..."

exec sudo "$LLDB" -b -p "$PID" \
  -o "command script import \"$TRACE_SCRIPT\"" \
  -o 'trace_os_log_once "CGLayerKit called back with no connection"' \
  -o quit
