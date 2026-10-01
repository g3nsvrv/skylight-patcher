#!/bin/bash
set -euo pipefail

if [ "$(/usr/bin/id -u)" -ne 0 ]; then
    echo "Run this uninstaller with sudo:" >&2
    echo "  sudo ./uninstall.sh" >&2
    exit 1
fi

/bin/launchctl bootout system/local.skylight-patch >/dev/null 2>&1 || true
/bin/rm -f /Library/LaunchDaemons/local.skylight-patch.plist
/bin/rm -f /usr/local/sbin/skylight-patch-monitor.sh
/bin/rm -f /usr/local/share/skylight-patcher/skylight_patcher.py
/bin/rmdir /usr/local/share/skylight-patcher >/dev/null 2>&1 || true
/bin/rm -f /var/log/skylight-patch.log

cat <<'EOF'
SkyLight patch daemon removed.

The current WindowServer process remains patched in RAM until it restarts.
Reboot macOS to return SkyLight to the original code immediately.
EOF
