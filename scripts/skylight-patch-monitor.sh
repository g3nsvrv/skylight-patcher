#!/bin/bash
set -u

LLDB="$(/usr/bin/xcrun -f lldb 2>/dev/null)" || exit 1
PATCHER="/usr/local/share/skylight-patcher/skylight_patcher.py"
LAST_PID=""

log() {
    echo "$(/bin/date '+%Y-%m-%d %H:%M:%S') $*"
}

log "SkyLight patch monitor started"

while true; do
    PID="$(/usr/bin/pgrep -x WindowServer | /usr/bin/head -n 1)"

    if [ -z "$PID" ]; then
        /bin/sleep 2
        continue
    fi

    if [ "$PID" = "$LAST_PID" ]; then
        /bin/sleep 5
        continue
    fi

    log "Detected WindowServer PID $PID"
    log "Attempting SkyLight patch"

    OUTPUT="$($LLDB \
        -b \
        -p "$PID" \
        -o "command script import $PATCHER" \
        -o 'quit' 2>&1)"
    RESULT=$?

    echo "$OUTPUT"

    if echo "$OUTPUT" | /usr/bin/grep -Eq 'PATCH SUCCESSFUL|SkyLight already patched'; then
        log "Patch verified for WindowServer PID $PID"
        LAST_PID="$PID"
    else
        log "Patch was not verified (LLDB exit status $RESULT); will retry"
    fi

    /bin/sleep 5
done
