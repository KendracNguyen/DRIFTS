#!/usr/bin/env bash
# One-time setup on the Pi. Safe to re-run after every deploy.
#   cd ~/drifts && bash deploy/install.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"
cd "$REPO"
echo "==> Installing DRIFTS v2 from $REPO for user $USER_NAME"

echo "==> System packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-picamera2 python3-opencv python3-numpy libncnn-dev bluez

echo "==> Enable Bluetooth and add user permissions"
sudo systemctl enable --now bluetooth
sudo rfkill unblock bluetooth || true
sudo usermod -aG bluetooth,video,gpio "$USER_NAME"

echo "==> Python virtual environment"
# --system-site-packages lets the venv see apt's picamera2, opencv, and numpy.
if [ ! -d .venv ]; then
  python3 -m venv --system-site-packages .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> systemd service"
sed -e "s|@USER@|$USER_NAME|g" -e "s|@REPO@|$REPO|g" deploy/drifts.service \
  | sudo tee /etc/systemd/system/drifts.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable drifts
sudo systemctl restart drifts

chmod +x scripts/*.sh || true
echo
echo "Done. DRIFTS v2 now starts on every boot."
echo "  Live log:       journalctl -u drifts -f"
echo "  Troubleshoot:   bash scripts/maint.sh on"
