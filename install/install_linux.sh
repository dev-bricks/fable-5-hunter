#!/usr/bin/env bash
# fable-5-hunter -- Linux Autostart Installer
# ======================================================
# Installs the hunter as a systemd user service when available.
# Falls back to a user crontab @reboot entry on smaller Linux hosts.
# No sudo/admin rights required.
#
# Usage:
#   bash install/install_linux.sh             # auto: systemd user, else cron
#   bash install/install_linux.sh --systemd   # force systemd user service
#   bash install/install_linux.sh --cron      # force crontab @reboot entry
#   bash install/install_linux.sh --status    # show current service/cron state
#   bash install/install_linux.sh --uninstall # remove systemd + cron entries
set -euo pipefail

LABEL="com.fable5hunter.agent"
SERVICE_NAME="$LABEL.service"
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT="$SCRIPT_DIR/fable_hunter.py"
PYTHON="${PYTHON:-$(command -v python3 || command -v python || true)}"
LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/fable5hunter"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$SYSTEMD_DIR/$SERVICE_NAME"
CRON_BEGIN="# BEGIN fable-5-hunter"
CRON_END="# END fable-5-hunter"
MODE="auto"
ACTION="install"

usage() {
    sed -n '1,18p' "$0" | sed 's/^# \{0,1\}//'
}

shell_quote() {
    printf "'%s'" "$(printf "%s" "$1" | sed "s/'/'\\\\''/g")"
}

can_use_systemd_user() {
    command -v systemctl >/dev/null 2>&1 || return 1
    systemctl --user show-environment >/dev/null 2>&1 || return 1
}

write_systemd_unit() {
    mkdir -p "$SYSTEMD_DIR" "$LOG_DIR"
    cat > "$UNIT" <<UNITEOF
[Unit]
Description=Fable 5 Hunter availability watcher
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$SCRIPT_DIR
ExecStart=$PYTHON $SCRIPT run
Restart=on-failure
RestartSec=120
Environment=PYTHONIOENCODING=utf-8
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin:/snap/bin
StandardOutput=append:$LOG_DIR/hunter.log
StandardError=append:$LOG_DIR/hunter.err.log

[Install]
WantedBy=default.target
UNITEOF
}

install_systemd() {
    can_use_systemd_user || {
        echo "systemd --user is not available in this session." >&2
        echo "Use --cron or log in through a normal user session." >&2
        exit 1
    }
    write_systemd_unit
    systemctl --user daemon-reload
    systemctl --user enable --now "$SERVICE_NAME"
    echo "Installed systemd user service: $SERVICE_NAME"
    echo "Logs: $LOG_DIR/hunter.log"
    echo "Status: systemctl --user status $SERVICE_NAME"
}

remove_cron_entry() {
    command -v crontab >/dev/null 2>&1 || return 0
    local current clean
    current="$(mktemp)"
    clean="$(mktemp)"
    crontab -l > "$current" 2>/dev/null || true
    awk -v begin="$CRON_BEGIN" -v end="$CRON_END" '
        $0 == begin {skip=1; next}
        $0 == end {skip=0; next}
        !skip {print}
    ' "$current" > "$clean"
    crontab "$clean"
    rm -f "$current" "$clean"
}

install_cron() {
    command -v crontab >/dev/null 2>&1 || {
        echo "crontab is not available. Install cron or use --systemd in a user session." >&2
        exit 1
    }
    mkdir -p "$LOG_DIR"
    local current clean line
    current="$(mktemp)"
    clean="$(mktemp)"
    crontab -l > "$current" 2>/dev/null || true
    awk -v begin="$CRON_BEGIN" -v end="$CRON_END" '
        $0 == begin {skip=1; next}
        $0 == end {skip=0; next}
        !skip {print}
    ' "$current" > "$clean"
    line="@reboot cd $(shell_quote "$SCRIPT_DIR") && $(shell_quote "$PYTHON") $(shell_quote "$SCRIPT") run >> $(shell_quote "$LOG_DIR/hunter.cron.log") 2>&1"
    {
        cat "$clean"
        echo "$CRON_BEGIN"
        echo "$line"
        echo "$CRON_END"
    } | crontab -
    rm -f "$current" "$clean"
    echo "Installed crontab @reboot entry for fable-5-hunter."
    echo "Logs: $LOG_DIR/hunter.cron.log"
}

uninstall_all() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
        systemctl --user daemon-reload 2>/dev/null || true
    fi
    rm -f "$UNIT"
    remove_cron_entry
    echo "Removed fable-5-hunter Linux autostart entries."
}

show_status() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user status "$SERVICE_NAME" --no-pager 2>/dev/null || true
    fi
    if command -v crontab >/dev/null 2>&1; then
        crontab -l 2>/dev/null | sed -n "/$CRON_BEGIN/,/$CRON_END/p" || true
    fi
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --systemd) MODE="systemd" ;;
        --cron) MODE="cron" ;;
        --uninstall) ACTION="uninstall" ;;
        --status) ACTION="status" ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
    esac
    shift
done

if [[ "$ACTION" == "uninstall" ]]; then
    uninstall_all
    exit 0
fi

if [[ "$ACTION" == "status" ]]; then
    show_status
    exit 0
fi

[[ -n "$PYTHON" ]] || { echo "python3/python not found in PATH"; exit 1; }
[[ -f "$SCRIPT" ]] || { echo "Script not found: $SCRIPT"; exit 1; }

case "$MODE" in
    systemd) install_systemd ;;
    cron) install_cron ;;
    auto)
        if can_use_systemd_user; then
            install_systemd
        else
            install_cron
        fi
        ;;
esac
