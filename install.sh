#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BASE="/usr/local/share/skylight-patcher"
MONITOR="/usr/local/sbin/skylight-patch-monitor.sh"
PLIST="/Library/LaunchDaemons/local.skylight-patch.plist"
LABEL="local.skylight-patch"

if [[ ${EUID} -ne 0 ]]; then
  exec sudo "$0" "$@"
fi

/usr/bin/python3 "$ROOT/scripts/patcher.py" --patch-dir "$ROOT/patches" --validate
/usr/bin/plutil -lint "$ROOT/scripts/local.skylight-patch.plist" >/dev/null

mkdir -p "$BASE/scripts" "$BASE/patches" /usr/local/sbin

install -m 0644 "$ROOT/scripts/patcher.py" "$BASE/scripts/patcher.py"
install -m 0644 "$ROOT/scripts/lldb_patch.py" "$BASE/scripts/lldb_patch.py"
install -m 0644 "$ROOT"/patches/*.patch "$BASE/patches/"
install -m 0755 "$ROOT/scripts/skylight-patch-monitor.sh" "$MONITOR"
install -m 0644 "$ROOT/scripts/local.skylight-patch.plist" "$PLIST"

chown -R root:wheel "$BASE"
chown root:wheel "$MONITOR" "$PLIST"

/bin/launchctl bootout system/"$LABEL" >/dev/null 2>&1 || true
/bin/launchctl bootstrap system "$PLIST"
/bin/launchctl kickstart -k system/"$LABEL"

echo "Installed modular SkyLight patcher."
echo "Logs: /var/log/skylight-patch.log"
echo "Status: sudo launchctl print system/$LABEL"
