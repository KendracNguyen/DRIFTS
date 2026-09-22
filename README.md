# DRIFTS — Raspberry Pi app

This is the Pi half of DRIFTS. It reads the eye (PERCLOS) and steering inputs, computes the fatigue score, and sends alert commands over Bluetooth LE to an ESP32 that drives the vibration motor and buzzer. It also serves `/api/status` for the mobile app.

```
Camera / IMU  ─►  Pi: score = 100 − (0.7·PERCLOS + 0.3·steering)  ─BLE─►  ESP32  ─►  motor + buzzer
                        │
                        └─ HTTP :5000 /api/status ─►  mobile app (Live mode)
```

| Score | Level | Sent to ESP32 (editable in `config.toml`) |
|---|---|---|
| ≥ 70 | OK | `STOP` |
| 40–70 | WARN | vibration pulses |
| < 40 | CRITICAL | strong vibration + buzzer pulses |

Right now the eye and steering inputs are **simulated**. The simulation cycles OK → WARN → CRITICAL every 2 minutes, so you can test the ESP32 link before the camera pipeline exists. The MPU6050 reader is written but hasn't been tested on hardware yet. To use it, set `steering = "imu"`.

---

## 1. Run it on your laptop first (no Pi needed)

In PowerShell, from this folder:

```powershell
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m drifts --sim --no-ble --debug
```

Open http://localhost:5000/api/status and the numbers should change every few seconds. Press **Ctrl+C** to stop.

Your laptop has Bluetooth, so once the ESP32 is flashed you can drop `--no-ble` and test the whole link from the laptop.

Run the tests with `.venv\Scripts\python -m pytest`.

## 2. Flash the ESP32

1. Arduino IDE → Boards Manager → install **esp32 by Espressif (3.x)**.
2. Open `esp32/drifts_esp32/drifts_esp32.ino`.
3. Set `MOTOR_PIN` and `BUZZER_PIN` to match your wiring. Keep the transistor and flyback-diode driver circuits.
4. Upload. Then open Serial Monitor at 115200 to watch the commands arrive.

To test from the laptop:
- `python tools/ble_scan.py` finds the ESP32. The line marked `<-- DRIFTS ESP32` is yours.
- `python tools/send_cmd.py "VIB 200 0 0"` buzzes the motor. Then run `python tools/send_cmd.py STOP`.

**Commands** (text, one per line):

| Command | Meaning |
|---|---|
| `PING` | heartbeat. The Pi sends it every second, and the ESP32 turns everything off if pings stop for 3 s |
| `STOP` | all outputs off |
| `VIB <duty 0-255> <on_ms> <off_ms>` | pulse the motor. `off_ms 0` = continuous |
| `BUZZ <hz> <on_ms> <off_ms>` | pulse the buzzer. `hz 0` = silent |

## 3. Put it on the Pi

**First time.** From this folder in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1 -Install
```

This copies the folder to `~/drifts` on the Pi and runs `deploy/install.sh`. The install script:
- installs the packages
- enables I2C and Bluetooth
- builds `.venv`
- opens port 5000 to local networks
- installs the **systemd service** that starts the app on every boot

**After each change:** run `.\deploy\deploy.ps1`. It copies the folder and restarts the app.

Once installed, the app starts about 20–30 s after the Pi gets power, with no login. If it crashes, it restarts in 3 s. It finds the ESP32 whenever the ESP32 comes on.

## 4. Troubleshooting on the Pi

The app runs in the background, so **SSH still works while it's running.** Nothing blocks you from logging in.

| You want to… | Run on the Pi |
|---|---|
| Watch the live log | `journalctl -u drifts -f` |
| See if it's running | `bash ~/drifts/scripts/maint.sh status` |
| Stop it and keep it off across reboots | `bash ~/drifts/scripts/maint.sh on` |
| Run it in your terminal with debug output | `bash ~/drifts/scripts/maint.sh run` (add `--sim`, `--no-ble`, etc.) |
| Go back to normal (runs now and at boot) | `bash ~/drifts/scripts/maint.sh off` |
| Restart after editing `config.toml` on the Pi | `sudo systemctl restart drifts` |

**How maintenance mode works:** the service only starts if `/boot/firmware/drifts-maintenance` does **not** exist. `maint.sh on` creates that file.

**If you can't SSH in at all:**
1. Pull the SD card and put it in any computer.
2. Open the small `bootfs` drive and create an empty file named `drifts-maintenance` (no extension).
3. Put the card back. The Pi boots with the app off.
4. Delete the file (or run `maint.sh off`) to go back to normal.

**In the car** there's no home Wi-Fi. Add your phone's hotspot as a second network on the Pi. Then the Pi joins it automatically, and you can SSH in and the app can poll `/api/status`:

```bash
sudo nmcli dev wifi connect "<hotspot name>" password "<password>"
```

## Layout

```
config.toml            thresholds, sources, alert commands, BLE name, API port
drifts/                the app  (python -m drifts)
  app.py               control loop: read -> score -> level -> ESP32
  fatigue.py           score formula + levels with hysteresis
  sources.py           sim / camera (TODO) / MPU6050 inputs
  ble_link.py          auto-reconnecting BLE link to the ESP32
  api.py               GET /api/status for the mobile app
deploy/                deploy.ps1 (laptop -> Pi), install.sh, drifts.service
scripts/maint.sh       maintenance mode on/off/run/status
tools/                 ble_scan.py, send_cmd.py
esp32/drifts_esp32/    Arduino sketch for the ESP32
tests/                 python -m pytest
```

## Still to do

- `CameraEyes` in `sources.py`: PERCLOS from picamera2 frames at 640×480.
- Validate `ImuSteering` on the real MPU6050, then tune `full_scale_dps`.
- Check that the `alerts` shape in `/api/status` (`seat`, `audio`, `voice`) matches what the mobile app reads.
