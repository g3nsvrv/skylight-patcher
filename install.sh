#!/bin/bash
set -euo pipefail

SUPPORTED_BUILD="22H730"
SCRIPT_DIR="$(cd -- "$(dirname -- "$0")" && pwd)"

if [ "$(/usr/bin/id -u)" -ne 0 ]; then
    echo "Run this installer with sudo:" >&2
    echo "  sudo ./install.sh" >&2
    exit 1
fi

if [ "$(/usr/bin/uname -s)" != "Darwin" ]; then
    echo "Error: this patch is for macOS only." >&2
    exit 1
fi

BUILD="$(/usr/bin/sw_vers -buildVersion)"
if [ "$BUILD" != "$SUPPORTED_BUILD" ]; then
    echo "Error: unsupported macOS build $BUILD." >&2
    echo "This release only supports Ventura 13.7.8 build $SUPPORTED_BUILD." >&2
    exit 1
fi

/usr/bin/xcrun -f lldb >/dev/null 2>&1 || {
    echo "Error: LLDB not found. Install Xcode Command Line Tools first." >&2
    exit 1
}

/usr/bin/plutil -lint "$SCRIPT_DIR/scripts/local.skylight-patch.plist" >/dev/null

/bin/mkdir -p /usr/local/share/skylight-patcher /usr/local/sbin
/usr/bin/install -o root -g wheel -m 0644 \
    "$SCRIPT_DIR/scripts/skylight_patcher.py" \
    /usr/local/share/skylight-patcher/skylight_patcher.py
/usr/bin/install -o root -g wheel -m 0755 \
    "$SCRIPT_DIR/scripts/skylight-patch-monitor.sh" \
    /usr/local/sbin/skylight-patch-monitor.sh
/usr/bin/install -o root -g wheel -m 0644 \
    "$SCRIPT_DIR/scripts/local.skylight-patch.plist" \
    /Library/LaunchDaemons/local.skylight-patch.plist

/bin/launchctl bootout system/local.skylight-patch >/dev/null 2>&1 || true
/bin/launchctl bootstrap system /Library/LaunchDaemons/local.skylight-patch.plist

cat <<'EOF'
Installed SkyLight patch daemon.

Check status with:
  sudo launchctl print system/local.skylight-patch

Follow the log with:
  sudo tail -f /var/log/skylight-patch.log

NOTE: LLDB must be allowed to attach to WindowServer. See README.md for the
required SIP Debugging Restrictions setting.
EOF
