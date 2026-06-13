#!/usr/bin/env bash
# fable-5-hunter -- macOS Autostart Installer (launchd)
# ======================================================
# Creates a LaunchAgent that starts the hunter at every login,
# keeps it alive (KeepAlive) and restarts it after a crash.
# Survives Mac reboots. No sudo/admin rights required.
#
# Usage:
#   bash install/install_macos.sh             # install + start
#   bash install/install_macos.sh --uninstall # stop + remove
set -euo pipefail

LABEL="com.fable5hunter.agent"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$SCRIPT_DIR/fable_hunter.py"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PYTHON="$(command -v python3 || true)"
LOG_DIR="$HOME/Library/Logs/fable5hunter"
# launchd starts with a minimal PATH that usually lacks Homebrew (/opt/homebrew/bin),
# so `claude` would not be found. Prepend the common tool dirs explicitly (a
# non-interactive login shell may not load Homebrew), then append the login PATH.
PYDIR="$(dirname "$PYTHON")"
USERPATH="/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:$PYDIR:$HOME/.local/bin:$(bash -lc 'printf %s "$PATH"' 2>/dev/null):/usr/bin:/bin:/usr/sbin:/sbin"

if [[ "${1:-}" == "--uninstall" ]]; then
    # bootout is the modern API (macOS Ventura+); fall back to unload for older systems
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || \
        launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "LaunchAgent $LABEL removed."
    exit 0
fi

[[ -n "$PYTHON" ]] || { echo "python3 not found in PATH"; exit 1; }
[[ -f "$SCRIPT" ]] || { echo "Script not found: $SCRIPT"; exit 1; }
mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$SCRIPT</string>
        <string>run</string>
    </array>
    <key>WorkingDirectory</key><string>$SCRIPT_DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key><false/>
    </dict>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONIOENCODING</key><string>utf-8</string>
        <key>PATH</key><string>$USERPATH</string>
    </dict>
    <key>StandardOutPath</key><string>$LOG_DIR/hunter.log</string>
    <key>StandardErrorPath</key><string>$LOG_DIR/hunter.err.log</string>
</dict>
</plist>
PLISTEOF

echo "Python : $PYTHON"
echo "Script : $SCRIPT"
echo "Plist  : $PLIST"

# Unload any existing instance first (ignore errors if not loaded)
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

# 'launchctl load' is deprecated since Ventura, so we use bootstrap.
if ! launchctl bootstrap "gui/$(id -u)" "$PLIST"; then
    echo "ERROR: launchctl bootstrap failed. Check the plist: $PLIST" >&2
    exit 1
fi
# bootstrap does not reliably trigger RunAtLoad immediately, so kickstart it.
# (without -k: starts only if not already running, no double start)
launchctl kickstart "gui/$(id -u)/$LABEL" 2>/dev/null || true

echo "LaunchAgent installed and started."
echo "Logs : $LOG_DIR/hunter.log"
echo "Status: python3 $SCRIPT status"
