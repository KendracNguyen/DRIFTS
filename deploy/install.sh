#!/usr/bin/env bash
# One-time setup on the Pi. Safe to re-run after every deploy.
#   cd ~/drifts && bash deploy/install.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"
cd "$REPO"
echo "==> Installing DRIFTS from $REPO for user $USER_NAME"

echo "==> System packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-picamera2 bluez i2c-tools

echo "==> Enable I2C (for the MPU6050) and Bluetooth"
sudo raspi-config nonint do_i2c 0 || true
sudo systemctl enable --now bluetooth
sudo rfkill unblock bluetooth || true
sudo usermod -aG bluetooth,i2c,gpio,video "$USER_NAME"

echo "==> Python virtual environment"
# --system-site-packages lets the venv see apt's picamera2/libcamera.
if [ ! -d .venv ]; then
  python3 -m venv --system-site-packages .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> Firewall: allow the status API (port 5000) from private networks"
if command -v ufw >/dev/null; then
  for net in 192.168.0.0/16 172.16.0.0/12 10.0.0.0/8; do
    sudo ufw allow from "$net" to any port 5000 proto tcp >/dev/null
  done
fi

echo "==> systemd service"
sed -e "s|@USER@|$USER_NAME|g" -e "s|@REPO@|$REPO|g" deploy/drifts.service \
  | sudo tee /etc/systemd/system/drifts.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable drifts
sudo systemctl restart drifts

chmod +x scripts/*.sh
echo
echo "Done. DRIFTS now starts on every boot."
echo "  Live log:       journalctl -u drifts -f"
echo "  Troubleshoot:   bash scripts/maint.sh on"
