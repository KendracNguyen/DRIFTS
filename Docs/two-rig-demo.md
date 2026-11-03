# Two-Rig Demo: Pi Zero 2 W + AI Camera vs Pi 5 + Day/Night Camera

The same app (`drifts.live`) and the same dashboard on two pieces of hardware,
side by side, to show what each can measure and how fast.

| | **Rig A** | **Rig B** |
| --- | --- | --- |
| Computer | Raspberry Pi Zero 2 W | Raspberry Pi 5 |
| Camera | Raspberry Pi AI Camera (Sony IMX500) | Arducam OV5647 day/night: auto IR-cut, IR LEDs, M12 lens |
| Face analysis | On the camera: stock pose network, **5 face points** | On the Pi 5 CPU: MediaPipe, **478 landmarks** incl. iris |
| Software | `drifts/imx500_pose.py` + `headpose.py` | [dms-face](https://github.com/youssefmedhat4/dms-face) (MIT) + `mouth.py`, `night.py` |
| Start with | `--pose imx500` | `--face mediapipe` |

## What each rig can deliver (the 8 signals)

| Signal | Rig A: Zero + AI Camera | Rig B: Pi 5 + OV5647 |
| --- | --- | --- |
| Off-road gaze | proxy (head direction only) | **live** (iris + head) |
| Yawn | n/a | **live** |
| Head yaw | **live** | **live** |
| Head pitch | rough | **live** |
| Head roll | **live** | **live** |
| Mouth aperture | n/a | **live** |
| Eye openness (EAR) | n/a | **live** |
| Blinking | n/a | **live** |
| **Count** | **2 live, 4 usable** | **8 live** |

Rig A's limit is the hardware: five face points (nose, eyes, ears) cannot give
eyelid or mouth shape. Stage 3 (a PFLD landmark network on the AI Camera)
would try to lift that; see [`ai-camera-stage2.md`](ai-camera-stage2.md).

**Night:** the AI Camera has a fixed IR-cut filter and needs visible light.
The Arducam module switches its IR-cut filter and IR LEDs **by itself** from
its own light sensor; Raspberry Pi OS no longer offers software control.
Rig B notices the switch (the image loses its colour) and shows
**Night (IR)**, applying contrast enhancement for the face model.

---

## Hardware notes

- **Pi 5 camera cable:** the Pi 5 has the narrow 22-pin camera connector
  (like the Zero). The Arducam module has the standard 15-pin socket, so you
  need a **Pi 5 camera cable (22-pin to 15-pin, "Standard-Mini")**.
- **IR LEDs** on the Arducam board are powered from the camera connector; no
  extra wiring. They are invisible, or show a faint red glow.
- **Focus** the M12 lens by hand at the driver's distance (~60-80 cm):
  loosen the lock ring, turn until the eyes are sharp on the dashboard, lock.
- **Hostnames:** give the two Pis different names in Raspberry Pi Imager,
  e.g. `drifts-zero` and `drifts-pi5`, so both are reachable at once.
- **Pi 5 cooling:** use the Active Cooler or a case fan. Temperature is shown
  on the dashboard.

---

## Setup

### Rig A: Pi Zero 2 W + AI Camera
See [`ai-camera-stage2.md`](ai-camera-stage2.md) (install with `AI_CAMERA=1`,
the 1-hour soak test, calibration).

### Rig B: Pi 5 + Arducam (64-bit Raspberry Pi OS)
```powershell
# laptop, repo's Raspberry folder
powershell -ExecutionPolicy Bypass -File .\deploy\deploy.ps1 -PiHost drifts-pi5.local -Install
```
```bash
# on the Pi 5
cd ~/drifts && PI5=1 bash deploy/install.sh        # dms-face + MediaPipe (lean, pinned)
sudo systemctl stop drifts
rpicam-hello -t 3000                               # camera check
.venv/bin/python ~/dms-face/tests/test_core.py     # dms-face self-test: 41 tests
```

**Calibrate the driver (once per person, about 20 s).** dms-face measures
your open eyes, closed eyes and neutral head pose:
```bash
cd ~/dms-face && ~/drifts/.venv/bin/python run.py --calibrate --profile kendra --no-display
```
Look at the road and blink normally, then close your eyes when asked. The
profile is saved in `~/dms-face/profiles/kendra.json`.

---

## Running the demo

Only **one** rig can be the ESP32's Pi over Bluetooth at a time. Give the
real ESP32 to one rig, and run the other with `--esp sim`.

```bash
# Rig B (Pi 5): drives the real ESP32, LEDs, LCD and buzzer
cd ~/drifts && .venv/bin/python -m drifts.live --face mediapipe --profile kendra \
    --esp ble --rig-name "Pi 5 + OV5647 day/night"

# Rig A (Zero 2 W)
cd ~/drifts && .venv/bin/python -m drifts.live --pose imx500 --no-eyes \
    --esp sim --rig-name "Pi Zero 2 W + AI Camera"
```

Open the **comparison page** on the laptop:
```
http://drifts-pi5.local:8080/compare?a=http://drifts-zero.local:8080&b=http://drifts-pi5.local:8080
```
Both live videos side by side, frame rate, inference time, CPU and temperature
per rig, and all eight signals marked LIVE / PROXY / ROUGH / N/A with their
live values. Each rig's own dashboard (`:8080`) also has a **Performance**
card and the signal list under Devices.

**Check the yaw direction once on Rig B:** turn your head to your right; the
dashboard's yaw should go **positive**. If it goes negative, restart with
`--yaw-sign -1`.

### Demo script (about 5 minutes)

| # | Do | What to point out |
| --- | --- | --- |
| 1 | Sit facing both cameras, room lights on | Both: ON ROAD. Rig B 8/8 live, Rig A 2/8 live (4 usable) |
| 2 | Turn your head to a mirror for 3 s | Both catch it: OFF ROAD, glance counted after 2 s |
| 3 | Keep your head still, look down with your **eyes only** | **Rig B** flags it (iris); **Rig A** does not: head-direction proxy only |
| 4 | Blink, then close your eyes for 2 s | Rig B: blink rate and duration, eye openness drops, EAR drops. Rig A: nothing |
| 5 | Yawn | Rig B: MAR rises, "YAWNING", count +1. Rig A: n/a |
| 6 | Read the performance tiles | Frame rate, inference ms, CPU %, temperature: Zero offloads to the sensor; Pi 5 does everything on the CPU |
| 7 | **Lights off** | Arducam switches to IR by itself: Rig B shows *Night (IR)* and keeps tracking. Rig A loses the face (IR-cut filter) unless a dim visible LED is on |
| 8 | Close your eyes for several seconds on Rig B | PERCLOS rises, eye score falls, the ESP32 alert score drops, LCD shows INTERVENTION 1/2, buzzer pattern |

### What to record for the report
Fill in from the dashboards (or the `/status.json` of each rig):

| | Rig A | Rig B |
| --- | --- | --- |
| Frame rate (fps) | | |
| Inference (ms) | | |
| CPU (%) | | |
| Temperature after 10 min (°C) | | |
| Signals live / usable | 2 / 4 | 8 / 8 |
| Face found in the dark (lights off) | | |

dms-face's own benchmark (`python bench.py --source image --resolutions`) gives
a clean compute-only number for the Pi 5.

---

## Credits and status

- Rig B's eye and head pipeline is **dms-face** by Youssef Medhat (MIT
  licence), installed unmodified at commit `7b32c63` by `deploy/install.sh`
  (not copied into this repository). DRIFTS adds mouth aperture / yawn,
  day/night detection, the dashboard and the ESP32 link.
- **Not yet run on the real hardware.** The adapter (`face_mediapipe.py`) is
  tested against a stand-in with dms-face's API shape, and the whole two-rig
  dashboard and comparison page were run end to end with a stand-in pipeline.
  The dms-face code itself was reviewed (MIT, pinned commit), not executed,
  in development: run its 41 tests on the Pi 5 first (above).
