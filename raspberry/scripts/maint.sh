#!/usr/bin/env bash
# Maintenance mode for DRIFTS on the Pi.
#
#   bash scripts/maint.sh on       stop the app and keep it off across reboots
#   bash scripts/maint.sh off      back to normal: app runs now and on every boot
#   bash scripts/maint.sh status   show whether maintenance mode is on
#   bash scripts/maint.sh run      run the app in this terminal with debug logs
#                                  (Ctrl+C to stop; implies maintenance mode)
set -euo pipefail

FLAG=/boot/firmware/drifts-maintenance
REPO="$(cd "$(dirname "$0")/.." && pwd)"

case "${1:-status}" in
  on)
    sudo touch "$FLAG"
    sudo systemctl stop drifts
    echo "Maintenance mode ON. The app is stopped and won't start at boot."
    ;;
  off)
    sudo rm -f "$FLAG"
    sudo systemctl start drifts
    echo "Maintenance mode OFF. The app is running and will start at boot."
    ;;
  run)
    sudo touch "$FLAG"
    sudo systemctl stop drifts
    echo "Running in the foreground (Ctrl+C to stop). Run 'maint.sh off' when done."
    cd "$REPO"
    shift || true
    exec .venv/bin/python -m drifts --debug "$@"
    ;;
  status)
    if [ -e "$FLAG" ]; then echo "Maintenance mode: ON"; else echo "Maintenance mode: OFF"; fi
    systemctl --no-pager --lines=0 status drifts || true
    ;;
  *)
    echo "usage: $0 {on|off|status|run [app options]}"; exit 1 ;;
esac
