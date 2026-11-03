#!/usr/bin/env bash
# Tokyo (Raspberry Pi 5) setup. Safe to re-run.
#   cd ~/drifts/Tokyo && bash install.sh     then reboot once
set -euo pipefail
cd "$(dirname "$0")"
CFG=/boot/firmware/config.txt

echo "==> System packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-lgpio python3-gpiozero python3-smbus2 i2c-tools \
  python3-picamera2 python3-opencv opencv-data python3-numpy python3-spidev alsa-utils bluez git

echo "==> I2C (LCD), SPI (LED strip on GPIO 10), hardware PWM (GPIO 12 IR LEDs, GPIO 13 motors)"
grep -q '^dtparam=i2c_arm=on' "$CFG" || echo 'dtparam=i2c_arm=on' | sudo tee -a "$CFG" >/dev/null
grep -q '^dtparam=spi=on' "$CFG" || echo 'dtparam=spi=on' | sudo tee -a "$CFG" >/dev/null
grep -q '^dtoverlay=pwm-2chan' "$CFG" || \
  echo 'dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4' | sudo tee -a "$CFG" >/dev/null
sudo usermod -aG gpio,i2c,spi,audio "$(whoami)"

echo "==> PWM sysfs access without sudo"
echo 'SUBSYSTEM=="pwm*", PROGRAM="/bin/sh -c '"'"'chgrp -R gpio /sys/class/pwm && chmod -R g+w /sys/class/pwm'"'"'"' \
  | sudo tee /etc/udev/rules.d/99-tokyo-pwm.rules >/dev/null

echo "==> LED strip, optional PIO backend (strip.backend = \"pio\"): /dev/pio0 access"
echo 'SUBSYSTEM=="*-pio", GROUP="gpio", MODE="0660"' | sudo tee /etc/udev/rules.d/99-tokyo-pio.rules >/dev/null
sudo udevadm control --reload-rules && sudo udevadm trigger || true
[ -e /dev/pio0 ] || echo "    (only for the pio backend) /dev/pio0 missing: 'sudo apt full-upgrade' and reboot"

echo "==> Python environment"
# --system-site-packages: gpiozero / lgpio / smbus2 come from apt.
[ -d .venv ] || python3 -m venv --system-site-packages .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo "==> Face pipeline: dms-face (MIT) at the reviewed commit + lean MediaPipe"
DMS_DIR="${DMS_FACE_DIR:-$HOME/dms-face}"
DMS_COMMIT=7b32c63a2c41554efb2a2f09d8d0a628b9b5630b
[ -d "$DMS_DIR/.git" ] || git clone -q https://github.com/youssefmedhat4/dms-face "$DMS_DIR"
git -C "$DMS_DIR" fetch -q origin && git -C "$DMS_DIR" checkout -q "$DMS_COMMIT"
.venv/bin/pip install -q --no-cache-dir --no-deps "mediapipe>=1.0.0"
.venv/bin/pip install -q --no-cache-dir -r "$DMS_DIR/requirements-lean.txt"

echo
echo "Done. Reboot once (sudo reboot), then:"
echo "  .venv/bin/python -m tokyo test all                 # outputs: buzzer, motors, strip, LCD, IR, voice"
echo "  aplay -l                                           # the USB speaker should be listed (found automatically)"
echo "  .venv/bin/python $DMS_DIR/tests/test_core.py       # face pipeline self-test"
echo "  cd $DMS_DIR && ~/drifts/Tokyo/.venv/bin/python run.py --calibrate --profile <name> --no-display"
echo "  .venv/bin/python -m tokyo run --profile <name>    # the full app"
