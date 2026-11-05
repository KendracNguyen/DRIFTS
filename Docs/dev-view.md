# Dev View: Full Face Analysis (Symposium Demo)

Turn on **Settings → Developer Mode** in the live dashboard. It turns
the page dark (maroon accents stay) and shows how DRIFTS reads the driver's
face. **Switch to driver view** (in the header) returns to the phone app. The dev
view also opens directly at `http://<address>:8080/#dev`.

```
┌──────────────────────────────┬──────────────────────────────────┐
│ camera + landmark overlay    │ ● EYES   EAR · blinking · gaze    │
│  wireframe (dim)             │          target box (on/off road) │
│  ● eyes    ● head pose       │ ──────────────────────────────── │
│  ● mouth   ● nose anchor     │ ● HEAD   yaw/pitch X-Y box, tilt  │
│  layer toggles               │ ──────────────────────────────── │
│                              │ ● MOUTH  MAR · yawn               │
│                              │ ──────────────────────────────── │
│                              │ ● NOSE   anchor (not scored)      │
├──────────────────────────────┴──────────────────────────────────┤
│ Drowsiness 23  [▮▮▮▮ weighted sum of the 8 ]  NONE→WARNING→INTERVENTION  Motors Buzzer Status │
└───────────────────────────────────────────────────────────────────────┘
```

- **Left: camera.** A dim face-mesh wireframe, with each group's landmarks in
  its own colour:
  - **Eyes** (blue): P1–P6 and the irises.
  - **Head pose** (amber): the face outline and the three head-pose arrows.
  - **Mouth** (violet): the lip points.
  - **Nose anchor** (rose): the nose tip and bridge.

  Each layer can be switched on or off.
- **Right: the signals, grouped by what they measure.** Each group's dot
  matches its colour on the face, and thin dividers separate the groups.
  - **Eyes:** eye openness (EAR, with the formula worked through live) and blinking (rate, PERCLOS).
    The gaze target is a dot in a box: inside the dashed ROAD zone means on
    road. Close-ups of both eyes have P1–P6 labelled.
  - **Head pose:** an X-Y box with yaw left/right across and pitch up/down
    vertically. The road zone is dashed, and the marker tilts with the roll (head tilt).
  - **Mouth:** MAR with the yawn threshold, the yawn count, and a mouth close-up.
  - **Nose anchor:** the reference point the head-pose arrows start from. It
    is not scored.

  Every scored signal shows how many points it adds to the composite.
- **Bottom bar:**
  - **Composite drowsiness score:** the weighted sum of the 8 signals, with a
    stacked bar showing each signal's share in its group colour.
  - **Glasses switch:** Auto / On / Off.
  - **DRIFTS alert tier:** NONE → WARNING → INTERVENTION.
  - **Outputs:** whether the motors and buzzer are active, and the status-light colour.

## Run it on a laptop first

Test on the laptop webcam before moving to the Pi:

```powershell
cd C:\Users\Kendra\Documents\School\Senior\DRIFTS\Raspberry
pip install mediapipe "opencv-python<5" bleak pyserial
python -m drifts.live --face mesh --camera 0 --esp sim --mirror
```
Then open http://localhost:8080 and turn on **Settings → Developer Mode**.

- The first run downloads the face model (3.7 MB, Apache 2.0) to `Raspberry/models/`.
- `--esp serial --port COM5` uses the real ESP32 over USB instead of `sim`.
  The ESP32 then receives the fused face score as `EYE:<n>`.
- `--glasses on` / `--glasses off` fixes the glasses setting from the start.
  Otherwise it is detected automatically and can be changed in the dev view.
- `--video clip.mp4` plays a recorded drive instead of the webcam.

## On the Pi
```bash
cd ~/drifts/Raspberry && MESH=1 bash deploy/install.sh    # MediaPipe + model (64-bit Pi OS)
sudo systemctl stop drifts
.venv/bin/python -m drifts.live --face mesh --esp uart    # wired to the ESP32 (--esp ble without the wires)
```
Open `http://<pi-address>:8080/#dev` from a laptop on the same network.

- **Pi Zero 2 W:** it works, but expect only a few frames per second. The
  overlay and close-ups are drawn only while the dev view is open, so a
  phone on the driver view doesn't pay for them.
- **For the symposium:** run the dev view on the laptop or the Pi 5 so the
  motion looks smooth. Keep the Zero for the in-car demo.

## The eight signals

| # | Signal | How it's measured | Flagged when |
| --- | --- | --- | --- |
| 1 | **Off-road gaze** | Iris position between the eye corners (relative to the calibrated road position), plus head pose. **Closed eyes count as off road.** Glances longer than 2 s are counted | Eyes closed, iris offset > 0.16 from the road position, or head off road |
| 2 | **Yawn** | MAR held above 0.50 for ≥ 1.5 s; counted over 10 min | Yawning |
| 3 | **Yaw** | Nose axis (MediaPipe's head-pose matrix); + = turned to the driver's right | Beyond ±30° from the road pose |
| 4 | **Pitch** | Nose axis; + = looking down. A drop of ≥ 12° that comes back within 3 s is a **nod**; staying down > 2 s is **droop** | Droop |
| 5 | **Roll** | Nose axis; + = tilted toward the right shoulder | — |
| 6 | **Mouth aperture** | MAR = mean of inner-lip openings (13–14, 82–87, 312–317) ÷ mouth width (78–308) | — |
| 7 | **Eye openness** | EAR = (‖P2−P6‖ + ‖P3−P5‖) ÷ 2‖P1−P4‖ per eye, averaged | Eyes closed |
| 8 | **Blinking** | A closure of 50–500 ms is a blink → rate per minute, mean duration. Longer closures are counted separately | Closed ≥ 1 s |

**PERCLOS (P80, 60 s):** the share of time the eyes are at least 80 % closed.
"Closed" is measured against the driver's own open-eye EAR (85th percentile of
the last minute), so it adapts to each face without a calibration step.

**Calibration.** Yaw, pitch and the iris position are all shown relative to
the **road pose**: what the driver looks like while looking at the road.
- It's learned automatically in the first few seconds with a face, or again
  when you press **Calibrate** (in the dev view's Head pose group, or on the
  driver view).
- Only frames with the **eyes open** are used, so a head that droops while
  the eyes close never becomes the "normal" pose.
- The dev view shows the progress ("calibrating 40 %: look at the road with
  your eyes open"), then "calibrated: head + eyes".

EAR P1–P6 use MediaPipe landmarks:

| | P1 | P2 | P3 | P4 | P5 | P6 |
| --- | --- | --- | --- | --- | --- | --- |
| Right eye | 33 | 160 | 158 | 133 | 153 | 144 |
| Left eye | 362 | 385 | 387 | 263 | 373 | 380 |

## Composite drowsiness score

Each of the 8 signals becomes a drowsiness part *d* from 0 (fine) to 1 (as
bad as it gets). The composite is their weighted sum, 0–100, where higher
means drowsier. The ESP32 receives **100 − composite** as `EYE:<n>`, so its
alert score and outputs follow it, and so does the Whisplay screen.

| Signal | d = 1 when | Weight | With glasses |
| --- | --- | --- | --- |
| EAR | PERCLOS 30 % | 0.30 | 0.12 |
| Blinking | mean blink 500 ms, or eyes shut 2 s | 0.15 | 0.06 |
| Gaze | irises off road for 3 s | 0.10 | 0.07 |
| Yaw | turned 40° from the driver's own neutral (starts at 15°) | 0.05 | 0.08 |
| Pitch | 3 nods in a minute, or 3 s of head droop | 0.15 | **0.30** |
| Head tilt (roll) | 30° from neutral (starts at 10°): the head lolling | 0.05 | **0.12** |
| MAR | mouth hanging open, MAR 0.3 → 0.7 | 0.05 | 0.07 |
| Yawn | 3 yawns in 10 min; a yawn in progress counts at least 0.6 | 0.15 | 0.18 |
| | | eyes 55 % · head 25 % · mouth 20 % | eyes 25 % · head 50 % · mouth 25 % |

Signals with no data yet are left out, and the remaining weights are
rescaled to 100 %. For example, PERCLOS needs 10 s of data before it counts.

**Alert tier** (bottom bar), from the ESP32's alert score, or from Tokyo's own
outputs:

| Tier | When | Outputs |
| --- | --- | --- |
| NONE | score ≥ 70 | status light green |
| WARNING | Intervention 1 (score 50–69) | status light yellow (Tokyo: buzzer) |
| INTERVENTION | Intervention 2 (score < 50) or the drowsy alert | buzzer + motors, status light red |

## Glasses: leaning on the nose axis

EAR is measured from six points on the eyelids. Lenses throw reflections,
frames cover the lid, and the lens bends where the lid appears, so EAR is less
reliable with glasses. In testing, drawing a frame on a photo changed its EAR
from 0.21 to 0.26 without the eyes moving at all.

**Detection.** The detector compares edge strength across the nose bridge
(where a glasses bridge sits) with the cheek next to it. In testing it read
about 0.0 on two faces without glasses, 0.7 on a real pair of glasses, and
1.0–1.6 on drawn frames. The threshold is 0.22, smoothed over 1.5 s.

**With glasses, the weights move from the eyes to the nose axis**, as in the
table above: pitch (nods and droop) and head tilt carry the most.

**Tuning on your own faces.** Use `--glasses on|off` or the Auto / On / Off
switch to fix the setting. The raw reading is in `/status.json` as
`glasses_level`. If the threshold doesn't separate your faces, adjust
`GlassesDetector(on_above=0.22, off_below=0.15)` in `drifts/facesignals.py`.

## Symposium: visitors on their phones (planned)

Visitors scan two printed QR codes (join our hotspot, open DRIFTS) and get a
view-only dashboard with the live camera. This gets built after the UI is
refined. The plan is in [symposium-viewers.md](symposium-viewers.md).

## Files
| | |
| --- | --- |
| `Raspberry/drifts/facesignals.py` | The signal maths: EAR, nose axis, iris, blinks, PERCLOS, nods, glasses, composite score. Pure Python, unit-tested |
| `Raspberry/drifts/facemesh.py` | MediaPipe face landmarker source, bridge-edge measurement, overlay and close-up drawing |
| `Raspberry/drifts/live.py` | `--face mesh`, `/dev.mjpg`, `/zoom.mjpg` (eyes), `/mouth.mjpg`, `POST /glasses`, `POST /dev_layers` |
| `Raspberry/drifts/dashboard.html` | The dev view |
| `Raspberry/tests/test_facesignals.py` | 20 tests with synthetic landmarks (no camera) |

**Tokyo** can use the same dev view: `python -m tokyo run --face mesh`.
