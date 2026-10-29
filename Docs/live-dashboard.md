# Live Dashboard: OBD + Eye Tracking

A web page that shows, side by side and live:

- the **driver camera** with the face and eyes boxed, **OPEN / CLOSED**,
  PERCLOS and the eye score;
- **OBD data** from the car (RPM, speed, throttle, dongle link and rate);
- the ESP32's **alert score, intervention level** and the **LCD text**;
- **training capture** buttons that save labelled frames for the eye model.

The eye score is sent to the ESP32 as `EYE:<n>` every 2 s, so closing your
eyes in front of the camera moves the real alert score, LEDs and LCD.

```
camera ──► eye tracker ──► PERCLOS ──► EYE:<n> ──► ESP32 ──► score, LEDs, LCD
                                                     │
car ──OBD──► Veepeak ──BLE──► ESP32 ──USB or BLE──► dashboard (browser)
```

Code: `Raspberry/drifts/live.py` (server), `dashboard.html` (the page),
`eyes.py` (tracker + PERCLOS), `esp_link.py` (USB serial / BLE / simulated
ESP32). The page is re-read on every load: edit `dashboard.html` and refresh.

### Screens (same design as the DRIFTS phone app)

A phone-width page in the app's maroon style, usable on a phone or a laptop.

| Tab | Shows |
| --- | --- |
| **Home** | Alertness score ring (ESP32 alert score) with Eyes / Driving / PERCLOS bars and the LCD text; live driver camera; active interventions; vehicle RPM / speed / throttle; nearest rest stop |
| **History** | Alert score, eye score and RPM for the whole session (kept by the server, so opening the tab mid-demo shows everything) |
| **Devices** | ESP32 link, firmware, OBD dongle, camera, LCD, eye scores sent, ESP32 log |
| **Training** | Capture buttons and counts |

Intervention pills: **ACTIVE** = the ESP32 is driving that output right now
(buzzer + motor during an alert); **LEVEL 1 / LEVEL 2** = the score has
reached that intervention level; **STANDBY** = not reached; **PLANNED** =
Level 3 voice and Level 4 rest-stop routing, not built yet. The rest-stop card
is a placeholder until phone GPS exists: it never shows a made-up place.

> ⚠ The eye tracker is a **demo heuristic** (OpenCV Haar cascades), not the
> trained model: about **74 %** right on frames where it finds a face in the
> Simuletic set, and it misses faces that turn away or droop. Present it as
> "live eye-state estimate; trained model in progress".

---

### Alarm and buzzer buttons

- **Alarm on this device** (Home): this phone or laptop beeps **1 s on /
  1 s off** (and vibrates on Android) while fatigue is detected. Switch it on
  by hand; browsers block sound until you tap. Used for the no-hardware
  simulation; see [`buzzer-alerts.md`](buzzer-alerts.md).
- **Test sound here**: one beep on this device.
- **Beep ESP32 buzzer**: sends `BEEP` (firmware v15+), one 1 s beep on the
  real buzzer.

## Tomorrow's demo: laptop setup (recommended)

No Pi needed. The laptop camera is the driver camera, and the ESP32 is on USB.

| Need | Notes |
| --- | --- |
| Laptop with webcam | Windows is fine |
| ESP32 on **v14**, USB to the laptop | LCD and LEDs wired as now |
| Veepeak in the car's OBD port, ignition ON or engine idling | ESP32 within ~2 m of the dongle |
| Phone apps closed | Torque, Car Scanner, nRF Connect: the dongle takes one link |

### Install (once, tonight)

```powershell
cd C:\Users\Kendra\Documents\School\Senior\DRIFTS
git fetch origin
git checkout pi-live-dashboard
cd Raspberry
pip install -r requirements-laptop.txt
```

### Run

Close miniterm first (one program per COM port), then:

```powershell
python -m drifts.live --esp serial --port COM5 --mirror
```

Open **http://localhost:8080** in a browser. Ctrl+C stops it.

### Demo script (about 5 minutes)

| # | Do | Audience sees |
| --- | --- | --- |
| 1 | Engine idling, page open | RPM ~700–900 updating every second, dongle `polling`, ~4–5 Hz |
| 2 | Blip the throttle (parked) | RPM and throttle jump live |
| 3 | Look at the camera normally | Green face box, **EYES OPEN**, PERCLOS low, eye score ~90–100, **Drive Safe** |
| 4 | Close your eyes for ~5 s, open them | PERCLOS climbs; eye score drops; alert score falls to **INTERVENTION 1**: yellow LED, LCD text changes |
| 5 | Keep eyes mostly closed ~8 s | **INTERVENTION 2**: red LED, LCD `INTERVENTION 2` |
| 6 | Eyes open again ~15 s | Recovers to Drive Safe (5-point hysteresis) |
| 7 | Eyes closed, press **C** (Save CLOSED); eyes open, press **O** | Counter goes up: frames are being collected to train the real model |

The ESP32 only raises its own **buzzer + motor alert** when the Pi says
DROWSY. To show the alert in the demo, use the alert test tool afterwards
(`python -m drifts.alert_test --serial COM5`, option **D**), or type
`DROWSY` in miniterm.

### If something goes wrong

| Problem | Fix |
| --- | --- |
| `could not open port 'COM5'` | Close miniterm / Arduino IDE; replug USB; `python -m serial.tools.list_ports -v` for the CH343 port |
| Black video / wrong camera | `--camera 1` (or 2). Check no other app (Teams, Zoom) holds the camera |
| Always **NO FACE** | Face the camera, light on your face (not behind you) |
| Always **CLOSED** with eyes open | Glasses glare or dim light: take glasses off, add light; or `--work-width 480` |
| OBD shows `scanning` | Ignition on? ESP32 near the dongle? Phone apps closed? |
| Want the demo without the car | `--esp sim` (made-up OBD numbers, everything else real) |
| Webcam fails entirely | `--video <file.mp4 or folder of images>` plays a recording instead |

---

## Raspberry Pi setup (in the car, stretch goal)

Same app on the Pi with the Pi camera, talking to the ESP32 over BLE.
**Not yet tested on hardware:** treat as a stretch for tomorrow.

From the laptop (PowerShell, in the repo's `Raspberry` folder):

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1 -Install   # first time
powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1            # later updates
```

Then on the Pi (`ssh drifts@drifts.local`):

```bash
sudo systemctl stop drifts          # frees the camera and the ESP32 link
cd ~/drifts
.venv/bin/python -m drifts.live --esp ble                # Pi camera + ESP32
.venv/bin/python -m drifts.live --esp sim --video sim_images   # no camera, no ESP32
```

For `--video`, copy sample frames first:
`scp -r ..\Simuletic_DMS_Dataset\images drifts@drifts.local:~/drifts/sim_images`.
`sudo systemctl start drifts` puts the normal service back.

From a phone on the same Wi-Fi: **http://<pi-address>:8080**
(`hostname -I` on the Pi shows the address). The BLE link also sends the PING
heartbeat, so the ESP32 fail-safe stays satisfied.

Expect ~5–8 fps on the Pi Zero 2 W; `--work-width 240` is faster.

---

## Options

| Option | Default | |
| --- | --- | --- |
| `--esp serial \| ble \| sim` | `sim` | ESP32 link |
| `--port COM5` | `COM5` | serial port (Linux: `/dev/ttyACM0` or `/dev/ttyUSB0`) |
| `--camera N` | 0 | webcam index |
| `--video PATH` | — | play a video file or image folder instead of a camera |
| `--window S` | 10 | PERCLOS window in seconds (shorter reacts faster) |
| `--eye-every S` | 2 | seconds between `EYE:<n>` sends |
| `--no-eye` | — | track and show, but do not send to the ESP32 |
| `--mirror` | — | selfie view |
| `--http-port` | 8080 | web port |
| `--capture-dir` | `captures` | where training frames go |

**Eye score** = 100 × (1 − PERCLOS). On the ESP32: alert = 0.7 × eye +
0.3 × driving, so with calm driving an eye score below ~29 gives
INTERVENTION 2 and below ~57 gives INTERVENTION 1.

---

## Training the real eye model from captures

The capture buttons write the same layout as the Simuletic dataset, which
`training/train.py` already reads:

```
captures/
  images/drifts_live_<session>_f00000.jpg ...
  labels/drifts_live_<session>.json      {"samples":[{"image":..., "attributes":{"eye_state":"Closed", ...}}]}
```

`captures/` is git-ignored: faces stay off GitHub.

1. **Collect.** Several people, glasses on and off, day and night lighting,
   in the car seat. Aim for **300+ frames per class** (Open, Closed,
   Drowsy). Use the ×10 bursts while holding a state.
2. **Train** on a GPU (Google Colab is enough):
   `python training/train.py --data-dir captures --epochs 30`
   (merge with `Simuletic_DMS_Dataset` by copying its `images/` and
   `labels/` in). Hold out one person's session for validation.
3. **Export** `python training/export_ncnn.py --weights runs/best_model.pth --output-dir model`.
4. **Deploy** the `.param` / `.bin` to the Pi (`deploy/deploy.ps1`).
5. **Next code step:** let `drifts.live` use `detector.py` (the trained
   model) instead of the Haar tracker when model files are present.
