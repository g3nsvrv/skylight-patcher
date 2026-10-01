#!/bin/bash
set -euo pipefail

BASE="/usr/local/share/skylight-patcher"
MONITOR="/usr/local/sbin/skylight-patch-monitor.sh"
PLIST="/Library/LaunchDaemons/local.skylight-patch.plist"
LABEL="local.skylight-patch"

if [[ ${EUID} -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

/bin/launchctl bootout system/"$LABEL" >/dev/null 2>&1 || true
rm -f "$PLIST" "$MONITOR"
rm -rf "$BASE"

echo "Uninstalled. Runtime patches already present in the current WindowServer remain until WindowServer restarts or the Mac reboots."
