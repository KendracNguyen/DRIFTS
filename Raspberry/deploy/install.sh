#!/usr/bin/env bash
# One-time setup on the Pi. Safe to re-run after every deploy.
#   cd ~/drifts/Raspberry && bash deploy/install.sh
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="$(whoami)"
cd "$REPO"
echo "==> Installing DRIFTS v2 from $REPO for user $USER_NAME"

echo "==> System packages"
sudo apt-get update -qq
# opencv-data holds the Haar cascades the live dashboard's eye tracker needs
# (python3-opencv alone does not ship them).
sudo apt-get install -y -qq python3-venv python3-picamera2 python3-opencv opencv-data python3-numpy python3-pil fonts-inter fonts-dejavu-core alsa-utils bluez

# Raspberry Pi AI Camera (Sony IMX500): firmware, model zoo and tools.
# Opt in with:  AI_CAMERA=1 bash deploy/install.sh   (takes 10-15 min on a Zero 2 W)
if [ "${AI_CAMERA:-0}" = "1" ]; then
  echo "==> AI Camera packages (imx500-all)"
  sudo apt-get install -y -qq imx500-all
fi

# Link to the ESP32: the UART wire (default, firmware v21+) or BLE.
#   LINK=ble bash deploy/install.sh   keeps the Pi's Bluetooth for the old BLE link
LINK="${LINK:-uart}"
sudo usermod -aG video,gpio,dialout "$USER_NAME"
if [ "$LINK" = "uart" ]; then
  echo "==> Wired ESP32 link: UART on GPIO 14/15 (pins 8/10)"
  BOOT_CFG=/boot/firmware/config.txt; [ -f "$BOOT_CFG" ] || BOOT_CFG=/boot/config.txt
  CMDLINE=/boot/firmware/cmdline.txt; [ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
  # The full UART (ttyAMA0) on pins 8/10, and the Pi's Bluetooth off: it is
  # no longer needed and it shares the radio with Wi-Fi.
  for line in "enable_uart=1" "dtoverlay=disable-bt"; do
    grep -qxF "$line" "$BOOT_CFG" || echo "$line" | sudo tee -a "$BOOT_CFG" >/dev/null
  done
  # No login console on the serial port: the ESP32 talks there.
  if command -v raspi-config >/dev/null; then
    sudo raspi-config nonint do_serial_cons 1 || true
  fi
  sudo sed -i -E 's/ ?console=(serial0|ttyAMA0|ttyS0),[0-9]+//g' "$CMDLINE"
  sudo systemctl disable --now hciuart 2>/dev/null || true
  sudo systemctl disable --now serial-getty@ttyAMA0.service serial-getty@ttyS0.service 2>/dev/null || true
  echo "    Reboot once for the UART change. Check: ls -l /dev/serial0  (-> ttyAMA0)"
else
  echo "==> BLE ESP32 link: enable Bluetooth"
  sudo systemctl enable --now bluetooth
  sudo rfkill unblock bluetooth || true
  sudo usermod -aG bluetooth "$USER_NAME"
  sed -i 's/^kind = "uart"/kind = "ble"/' config.toml
fi

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

# Face mesh (first model, all eight signals):
# MediaPipe's face landmarker, no calibration profile. Opt in with:
#   MESH=1 bash deploy/install.sh      (64-bit Pi OS; the Pi 5 rig's PI5=1 also works)
# --no-deps keeps out MediaPipe's second OpenCV and audio stack; the four
# packages it does need at import are listed explicitly.
if [ "${MESH:-0}" = "1" ] || [ "${PI5:-0}" = "1" ]; then
  echo "==> MediaPipe face landmarker"
  sudo apt-get install -y -qq python3-matplotlib
  .venv/bin/pip install -q --no-cache-dir --no-deps "mediapipe>=1.0.0"
  .venv/bin/pip install -q --no-cache-dir absl-py flatbuffers certifi
  .venv/bin/python -c "from drifts.facemesh import ensure_model; print('    model:', ensure_model())"
  echo "    Run:  .venv/bin/python -m drifts.live --face mesh --esp uart"
fi

# Whisplay HAT (driver screen, RGB LED, voice, button): PiSugar's driver.
# Opt in with:  WHISPLAY=1 bash deploy/install.sh   then set [whisplay] enabled = true
if [ "${WHISPLAY:-0}" = "1" ]; then
  echo "==> PiSugar Whisplay HAT driver"
  sudo apt-get install -y -qq git
  [ -d "$HOME/Whisplay/.git" ] || git clone -q --depth 1 https://github.com/PiSugar/Whisplay.git "$HOME/Whisplay"
  (cd "$HOME/Whisplay" && git pull -q --ff-only || true; sudo bash install_driver.sh)
  # PiSugar's driver uses gpiod + spidev; the venv sees apt's copies (--system-site-packages)
  sudo apt-get install -y -qq python3-libgpiod python3-spidev
  .venv/bin/python -c "import gpiod, spidev" 2>/dev/null || .venv/bin/pip install -q spidev gpiod
  sudo usermod -aG gpio,spi,audio "$USER_NAME"
  # PiSugar's app launcher and AI chatbot take over the HAT: keep them off
  sudo systemctl disable --now whisplay-daemon.service chatbot.service 2>/dev/null || true
  echo "    Reboot, then:  .venv/bin/python -m drifts.whisplay_check --hw"
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
