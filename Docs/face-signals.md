# Face Signals: What the Camera Measures

The camera feeds MediaPipe's Face Landmarker (478 points on the face). DRIFTS
turns those points into a handful of numbers (eye openness, mouth opening,
head angles, blinks) and then drops the frame. Nothing is drawn, saved or
uploaded. The phone app's **"Worried about what the camera is tracking?"**
panel shows those numbers live, the points they come from, and every message
sent to the ESP32: see [`privacy-and-data.md`](privacy-and-data.md).

## Calibrate and test on a laptop

Only the laptop's webcam is needed: no Pi, no ESP32.

```powershell
cd C:\Users\Kendra\Documents\School\Senior\DRIFTS-repo\Raspberry
python -m drifts.live --face mesh --camera 0 --esp sim --mirror --profile kendra --http-port 8090
```
Open http://localhost:8090 (the phone app) and go to **Settings → Calibration**:

1. **Quick calibration:** about 3 s looking at the road; sets the road pose.
2. **Create profile:** enter a name and hold each of the nine poses for 2 s:
   eyes closed, eyes open, mouth closed, mouth open, left, right, up, down,
   forward. The profile (your eye scale, yawn threshold, off-road limits and
   road pose) is saved to `Raspberry/profiles/<name>.json` and applied at once.
   `--profile <name>` loads it next time.
3. **Accuracy test:** the button next to Start. Seven prompts, 4 s each (eyes
   open, eyes closed, look left, look right, look down, mouth open, back to the
   road); every frame is scored against what was asked. The result shows on
   the page and is saved to `Raspberry/logs/accuracy_<name>_<time>.json`.

To see the camera working while you do this, open the privacy panel on Home:
the points and numbers move with your face.

> MediaPipe's GPU delegate isn't available in Python on Windows, so the laptop
> runs on the CPU (about 15–40 ms a frame). `--face-delegate gpu` tries the GPU
> on Linux / macOS and falls back to the CPU.

## When the camera sets off the alert

The ESP32's alert score is 0.7 × eye + 0.3 × driving. Parked, or without the
OBD dongle, the driving part stays near 100. So the eye part alone stops around
60 (CAUTION), even with the eyes closed. `drifts.live` therefore also confirms
drowsiness itself:
- **`DROWSY`:** sent once the face mesh says **drowsy** (eyes closed ≥ 1 s, or
  drowsiness > 50) for 1.5 s, and again every 10 s while it lasts. The ESP32
  then runs the buzzer (1 s on / 1 s off) and the motors.
- **`NOT_DROWSY`:** sent after 3 s of being alert again.
- **Requirements:** a real ESP32 (`--esp serial --port COM5` on the laptop,
  `--esp uart` on the Pi; `--esp sim` reaches no hardware) and the
  "Send eye score to ESP32" switch on. `--no-drowsy` turns this off.

## The eight signals

| # | Signal | How it's measured | Flagged when |
| --- | --- | --- | --- |
| 1 | **Off-road gaze** | Iris position between the eye corners, plus head pose. Glances longer than 2 s are counted | Iris offset > 0.16, head off road, or eyes closed |
| 2 | **Yawn** | MAR held above the yawn threshold (0.50, or the calibrated one) for ≥ 1.5 s; counted over 10 min | Yawning |
| 3 | **Yaw** | Nose axis (MediaPipe's head-pose matrix); + = turned to the driver's right | Beyond the off-road limit (30°, or calibrated) |
| 4 | **Pitch** | Nose axis; + = looking down. A drop of ≥ 12° that comes back within 3 s is a **nod**; staying down > 2 s is **droop** | Droop |
| 5 | **Roll** | Nose axis; + = tilted toward the right shoulder | — |
| 6 | **Mouth aperture** | MAR = mean of inner-lip openings ÷ mouth width | — |
| 7 | **Eye openness** | EAR = (‖P2−P6‖ + ‖P3−P5‖) ÷ 2‖P1−P4‖ per eye, averaged | Eyes closed |
| 8 | **Blinking** | A closure of 50–500 ms is a blink → rate per minute, mean duration | Closed ≥ 1 s |

**Eye openness scale:**
- Without a profile, "closed" is measured against the driver's own open-eye EAR (the 85th percentile of the last minute).
- With a calibration profile, the measured open and closed EAR are used directly.
- PERCLOS (P80, 60 s) is the share of time the eyes are at least 80 % closed.

EAR P1–P6 use MediaPipe landmarks:

| | P1 | P2 | P3 | P4 | P5 | P6 |
| --- | --- | --- | --- | --- | --- | --- |
| Right eye | 33 | 160 | 158 | 133 | 153 | 144 |
| Left eye | 362 | 385 | 387 | 263 | 373 | 380 |

## Composite drowsiness score

Each signal becomes a part *d* from 0 (fine) to 1 (as bad as it gets). The
composite is the weighted sum, 0–100 (higher = drowsier). The ESP32 receives
**100 − composite** as `EYE:<n>`.

| Signal | d = 1 when | Weight | With glasses |
| --- | --- | --- | --- |
| EAR | PERCLOS 30 % | 0.30 | 0.12 |
| Blinking | mean blink 500 ms, or eyes shut 2 s | 0.15 | 0.06 |
| Gaze | irises off road for 3 s | 0.10 | 0.07 |
| Yaw | turned 40° from neutral (starts at 15°) | 0.05 | 0.08 |
| Pitch | 3 nods in a minute, or 3 s of droop | 0.15 | **0.30** |
| Head tilt (roll) | 30° from neutral (starts at 10°) | 0.05 | **0.12** |
| MAR | mouth hanging open, MAR 0.3 → 0.7 | 0.05 | 0.07 |
| Yawn | 3 yawns in 10 min; a yawn in progress counts at least 0.6 | 0.15 | 0.18 |

Glasses are detected from the edge strength across the nose bridge
(`GlassesDetector` in `drifts/facesignals.py`), or set with Options → Glasses.

## Files
| | |
| --- | --- |
| `Raspberry/drifts/facemesh.py` | MediaPipe Face Landmarker: landmarks, blendshapes, head-pose matrix. Frames are analysed in memory and dropped |
| `Raspberry/drifts/facesignals.py` | the signal maths: EAR, blinks, PERCLOS, nose axis, composite score |
| `Raspberry/drifts/guided.py` | guided calibration, profile, accuracy test |
| `Raspberry/drifts/drowsy_trigger.py` | camera-confirmed DROWSY / NOT_DROWSY to the ESP32 |
| `Raspberry/drifts/live.py` | the phone app server: `/status.json`, `/privacy.json`, `/stream.mjpg` (only while open), `POST /guided` |
| `Raspberry/tests/test_guided.py`, `test_facesignals.py`, `test_drowsy_trigger.py`, `test_privacy.py` | tests with synthetic data (no camera) |
