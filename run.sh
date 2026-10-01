#!/bin/zsh
set -u

SCRIPT_DIR="${0:A:h}"
PATCHER="$SCRIPT_DIR/scripts/skylight_patcher.py"
LLDB="$(/usr/bin/xcrun -f lldb 2>/dev/null)" || {
  echo "Error: LLDB not found. Install Xcode Command Line Tools." >&2
  exit 1
}

[[ -f "$PATCHER" ]] || {
  echo "Error: patcher not found: $PATCHER" >&2
  exit 1
}

PID="$(/usr/bin/pgrep -x WindowServer | /usr/bin/head -n 1)"
[[ -n "$PID" ]] || {
  echo "Error: WindowServer is not running." >&2
  exit 1
}

exec sudo "$LLDB" \
  -b \
  -p "$PID" \
  -o "command script import $PATCHER" \
  -o 'quit'
