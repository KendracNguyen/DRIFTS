# AI Camera, Stage 2: Head Pose and Road Attention

Plan (from [`ai-camera-evaluation.md`](ai-camera-evaluation.md)), hardware
fixed at **Pi Zero 2 W + Raspberry Pi AI Camera (Sony IMX500)**:

| Stage | What | Status |
| --- | --- | --- |
| 1 | Buy one AI Camera, run Sony's Zero 2 W walkthrough, **1-hour soak test** | Waiting for hardware |
| **2** | **Head pose (yaw / pitch / roll) + road attention on the stock pose network** | **Software ready** (untested on the camera) |
| 3 | PFLD face landmarks for EAR, blinking, mouth aperture, yawn | Research; OV5647 is the fallback |

The pose network runs **on the camera**. The Pi only decodes 17 keypoints,
of which five are on the face (nose, eyes, ears), and turns them into head
angles. That leaves the Zero 2 W's CPU free.

---

## What stage 2 gives you

| Your category | Signal | Stage 2 | How |
| --- | --- | --- | --- |
| Road attention & gaze | Off-road gaze | ✅ head-direction proxy | Off road when yaw > ±30° or pitch > ±20° from the calibrated road pose. A glance counts once it lasts **2 s** (NHTSA visual-manual distraction guideline) |
| | Yawn | ⏳ stage 3 | Needs mouth points (PFLD) |
| Head position | Yaw | ✅ good | Nose offset from the eye midpoint, relative to eye spacing |
| | Roll | ✅ good | Angle of the line between the eyes |
| | Pitch | ⚠️ rough | Nose drop below the eye line; weakest with 5 points |
| Eye & mouth | Eye openness | ⚠️ rough | The existing Haar estimate (optional, costs CPU) |
| | EAR, blinking, mouth aperture | ⏳ stage 3 | Need ~6 points per eye and mouth points (PFLD) |

"Gaze" here means **where the head points**. It can't catch "eyes down,
head forward", because there are no iris points.

### How the angles are computed
`drifts/headpose.py` fits a small 3-D face model (eyes, nose tip, ears, in
proportion to eye spacing) to the keypoints. Three points can fit two poses
equally well when the head is both turned and nodded. The ears and the
previous frame pick the right one: in tests the worst error was under 1.5°
over ±50° yaw, −20…+30° pitch and ±20° roll, with ears visible or tracking
frame to frame. The camera is never straight in front of the driver, so the
first ~3 s with a face (or the **Calibrate** button) define the "looking at
the road" pose, and angles are reported relative to it.

Signs: **yaw + = driver's right, pitch + = looking down, roll + = tilt to
the right shoulder.**

---

## Setup on the Pi Zero 2 W

Follow Sony's walkthrough first:
[Tiny but mighty: AI Camera on Pi Zero 2 W](https://www.hackster.io/sony-aitrios/tiny-but-mighty-raspberry-pi-ai-camera-on-pi-zero-2-w-68c15b).
Use **Raspberry Pi OS Lite 64-bit**. The camera needs the Zero's narrow
22-pin ribbon cable.

```powershell
# laptop, in the repo folder
powershell -ExecutionPolicy Bypass -File .\Raspberry\deploy\deploy.ps1 -Install
```
```bash
# on the Pi
cd ~/drifts/Raspberry && AI_CAMERA=1 bash deploy/install.sh   # adds imx500-all (10-15 min) + munkres
sudo systemctl stop drifts
```

### First hour: soak test (do this before anything else)
picamera2 issue [#1404](https://github.com/raspberrypi/picamera2/issues/1404)
reports a permanent hang on exactly this pairing after ~1,500 frames. The
maintainer could not reproduce it and suspects power delivery. A frozen
fatigue monitor is a safety problem, so test it:

```bash
.venv/bin/python -m drifts.imx500_pose --soak 3600
```
It prints frames, fps, how often the driver is seen and the on-sensor
inference time every 30 s. It exits with **PASS**, or **HANG** after 10 s
without a frame. On HANG, try the official 5 V 2.5 A supply, then
`sudo apt full-upgrade`, and note `uname -r`.

The first run loads the network onto the sensor. On a Zero 2 W this goes
over I²C and can take **minutes**; later runs use the camera's cache.

Quick angle check, once a second:
```bash
.venv/bin/python -m drifts.imx500_pose --print-pose
```

### Run the dashboard with head pose
```bash
.venv/bin/python -m drifts.live --esp ble --pose imx500 --no-eyes
```
Open `http://drifts.local:8080`. The new **Road attention & gaze**, **Head
position** and **Eye & mouth** cards are on Home, and the History tab has a
head-yaw chart. Look at the road for 3 s after starting, or press
**Calibrate**.

- `--no-eyes` skips the Haar eye tracker, which is the CPU-heavy part on the
  Zero. Leave it out if you want the rough open/closed estimate.
- Tuning: `--yaw-limit 30 --pitch-limit 20 --pose-fps 15 --calib-frames 30`.

### Without the AI Camera (laptop, today)
```powershell
python -m drifts.live --esp sim --pose sim --mirror
```
`--pose sim` drives a **scripted** head (road, mirror check, phone glance,
tilt, long look away) over any camera or `--video`, labelled *SIMULATED
HEAD*. It exercises every card and the attention logic. It does not track
your real head.

---

## Low-light and night: can we shine an LED near the camera?

Yes, but the **kind** of light depends on the camera:

| Camera | Infrared LEDs (850 / 940 nm) | Visible LED |
| --- | --- | --- |
| **AI Camera (IMX500)** | ❌ No effect: the built-in IR-cut filter blocks them | ✅ Works |
| **OV5647 NoIR** (fallback) | ✅ Best option: invisible (940 nm) or a faint red glow (850 nm) | Works, not needed |

**For the AI Camera, use a dim visible light:**
- **Warm white or amber, diffused**, with frosted tape or a small diffuser.
  Never a bare point source.
- **Placement:** beside or below the camera, angled at the face from about
  30° or more off the camera's axis. That avoids glare in glasses and keeps
  the light out of the driver's direct line of sight.
- **As dim as possible:** use PWM and find the lowest level where the
  dashboard still sees the face. Bright light in a dark cabin ruins the
  driver's night vision and distracts. That is why commercial driver
  monitors use infrared.
- **Wiring:** drive a small LED from 5 V through a MOSFET. Don't power it from
  a GPIO pin, which can't supply enough current. The ESP32 has spare pins (19,
  20, 23) with PWM that could dim it, as a later firmware addition.
- **Test it first:** your desktop and phone camera rig already works in low
  light. Check the AI Camera the same way (`--print-pose`, driver-seen %)
  before adding any light at all.

**If night use becomes required**, the OV5647 NoIR with IR LEDs (already the
stage 3 fallback) is the better path: invisible light, no glare. The trade-off
is that the landmark model would then need IR training images.

---

## Stage 3 preview (PFLD)

Replace the pose network with a single-stage PFLD face-landmark network
(98 points) fed a **face crop** set with `set_inference_roi_abs`. Use a
fixed head zone, or track the previous frame's landmarks. That gives EAR,
blinks, mouth aperture and yawns, and a better head pose via `solvePnP`.
Plan it with a deadline (e.g. 2 weeks) and pass criteria: eye points good
enough to separate open from closed, and at least 15 fps for blinks. If it
fails, fall back to the OV5647. See the evaluation for operator support,
the conversion toolchain and its traps.
