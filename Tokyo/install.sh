#!/usr/bin/env bash
# Tokyo (Raspberry Pi 5) setup. Safe to re-run.
#   cd ~/drifts/Tokyo && bash install.sh     then reboot once
set -euo pipefail
cd "$(dirname "$0")"
CFG=/boot/firmware/config.txt

echo "==> System packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3-venv python3-lgpio python3-gpiozero python3-smbus2 i2c-tools

echo "==> I2C and hardware PWM (GPIO 12 motors, GPIO 13 buzzer)"
grep -q '^dtparam=i2c_arm=on' "$CFG" || echo 'dtparam=i2c_arm=on' | sudo tee -a "$CFG" >/dev/null
grep -q '^dtoverlay=pwm-2chan' "$CFG" || \
  echo 'dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4' | sudo tee -a "$CFG" >/dev/null
sudo usermod -aG gpio,i2c "$(whoami)"

echo "==> PWM sysfs access without sudo"
echo 'SUBSYSTEM=="pwm*", PROGRAM="/bin/sh -c '"'"'chgrp -R gpio /sys/class/pwm && chmod -R g+w /sys/class/pwm'"'"'"' \
  | sudo tee /etc/udev/rules.d/99-tokyo-pwm.rules >/dev/null

echo "==> LED strip: Pi 5 PIO device (/dev/pio0) access"
echo 'SUBSYSTEM=="*-pio", GROUP="gpio", MODE="0660"' | sudo tee /etc/udev/rules.d/99-tokyo-pio.rules >/dev/null
sudo udevadm control --reload-rules && sudo udevadm trigger || true
[ -e /dev/pio0 ] || echo "    /dev/pio0 missing: run 'sudo apt full-upgrade' (newer firmware/kernel) and reboot"

echo "==> Python environment"
# --system-site-packages: gpiozero / lgpio / smbus2 come from apt.
[ -d .venv ] || python3 -m venv --system-site-packages .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

echo
echo "Done. Reboot once (sudo reboot), then:"
echo "  .venv/bin/python -m tokyo test all"
