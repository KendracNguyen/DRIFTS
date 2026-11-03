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
# opencv-data holds the Haar cascades the live dashboard's eye tracker needs
# (python3-opencv alone does not ship them).
sudo apt-get install -y -qq python3-venv python3-picamera2 python3-opencv opencv-data python3-numpy bluez

# Raspberry Pi AI Camera (Sony IMX500): firmware, model zoo and tools.
# Opt in with:  AI_CAMERA=1 bash deploy/install.sh   (takes 10-15 min on a Zero 2 W)
if [ "${AI_CAMERA:-0}" = "1" ]; then
  echo "==> AI Camera packages (imx500-all)"
  sudo apt-get install -y -qq imx500-all
fi

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

# Pi 5 rig (all eight face signals): dms-face + MediaPipe, pinned to the
# commit reviewed for DRIFTS. Opt in with:  PI5=1 bash deploy/install.sh
# MediaPipe goes in with --no-deps (its matplotlib / second OpenCV are not
# needed; see dms-face's requirements-lean.txt). Needs 64-bit Pi OS.
if [ "${PI5:-0}" = "1" ]; then
  DMS_DIR="${DMS_FACE_DIR:-$HOME/dms-face}"
  DMS_COMMIT=7b32c63a2c41554efb2a2f09d8d0a628b9b5630b
  echo "==> dms-face ($DMS_COMMIT) into $DMS_DIR"
  sudo apt-get install -y -qq git
  [ -d "$DMS_DIR/.git" ] || git clone -q https://github.com/youssefmedhat4/dms-face "$DMS_DIR"
  git -C "$DMS_DIR" fetch -q origin
  git -C "$DMS_DIR" checkout -q "$DMS_COMMIT"
  .venv/bin/pip install -q --no-cache-dir --no-deps "mediapipe>=1.0.0"
  .venv/bin/pip install -q --no-cache-dir -r "$DMS_DIR/requirements-lean.txt"
  echo "    Self-test:  .venv/bin/python $DMS_DIR/tests/test_core.py"
  echo "    Calibrate:  cd $DMS_DIR && ~/drifts/.venv/bin/python run.py --calibrate --profile <name> --no-display"
fi

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
