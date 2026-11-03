# Dev View: Full Face Analysis (Symposium Demo)

The live dashboard has a **Switch to dev view** button in the header. It
opens a wide page that shows how DRIFTS reads the driver's face:

- **Face analysis feed.** 478 landmarks, the EAR points P1–P6 on each eye, the
  mouth points, the irises, the glasses box across the nose bridge, and the
  **nose axis**: three arrows from the nose tip showing where the head points.
  Each layer can be switched on or off.
- **Eyes and mouth up close.** Zoomed crops with P1–P6 labelled, and the EAR
  and MAR formulas worked through with the live pixel distances.
- **The eight driver signals.** Each has a live value, a 60-second chart with
  its threshold, and a one-line note on how it's measured.
- **Glasses-aware fusion.** Whether glasses are detected, the eye/head
  weighting, and the face score that goes to the ESP32.

**Switch to driver view** returns to the normal phone dashboard. The dev
view also opens directly at `http://<address>:8080/#dev`.

## Run it on a laptop first

Test on the laptop webcam before moving to the Pi:

```powershell
cd C:\Users\Kendra\Documents\School\Senior\DRIFTS\Raspberry
pip install mediapipe "opencv-python<5" bleak pyserial
python -m drifts.live --face mesh --camera 0 --esp sim --mirror
```
Then open http://localhost:8080 and click **Switch to dev view**.

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
.venv/bin/python -m drifts.live --face mesh --esp ble
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
| 1 | **Off-road gaze** | Iris position between the eye corners, plus head pose. Glances longer than 2 s are counted | Iris offset > 0.16, or head off road |
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

**Yaw and pitch** are shown relative to the **road pose**: the median of the
first seconds with a face, or the **Calibrate** button.

EAR P1–P6 use MediaPipe landmarks:

| | P1 | P2 | P3 | P4 | P5 | P6 |
| --- | --- | --- | --- | --- | --- | --- |
| Right eye | 33 | 160 | 158 | 133 | 153 | 144 |
| Left eye | 362 | 385 | 387 | 263 | 373 | 380 |

## Glasses: leaning on the nose axis

EAR is measured from six points on the eyelids. Lenses throw reflections,
frames cover the lid, and the lens bends where the lid appears, so EAR is less
reliable with glasses. In testing, drawing a frame on a photo changed its EAR
from 0.21 to 0.26 without the eyes moving at all.

**Detection.** The detector compares edge strength across the nose bridge
(where a glasses bridge sits) with the cheek next to it (bare skin, same
light). In testing it read about 0.0 on two faces without glasses, 0.7 on a
real pair of glasses, and 1.0–1.6 on drawn frames. The threshold is 0.22, and
the reading is smoothed over 1.5 s, so it doesn't flicker.

**What changes with glasses:**

| | Eye weight | Head weight |
| --- | --- | --- |
| No glasses | 80 % | 20 % |
| **Glasses** | **40 %** | **60 %** |

```
face score = w_eye × eye score + w_head × head score        → ESP32 as EYE:<n>
eye score  = 100 × (1 − PERCLOS)
head score = 100 − 20 per nod in the last minute − 20 per second of droop
```
If one part has no data yet, the other is used alone. The dev view shows which
case applies.

**Tuning on your own faces.**
- The **Glasses** meter in the dev view shows the live reading.
- Note the reading with and without glasses for each team member, then adjust
  `GlassesDetector(on_above=0.22, off_below=0.15)` in `drifts/facesignals.py`
  if needed.
- For the demo, **On / Off** overrides the detection, so the fusion can be
  shown either way.

## Files
| | |
| --- | --- |
| `Raspberry/drifts/facesignals.py` | The signal maths: EAR, nose axis, iris, blinks, PERCLOS, nods, glasses, fusion. Pure Python, unit-tested |
| `Raspberry/drifts/facemesh.py` | MediaPipe face landmarker source, bridge-edge measurement, overlay and close-up drawing |
| `Raspberry/drifts/live.py` | `--face mesh`, `/dev.mjpg`, `/zoom.mjpg`, `POST /glasses`, `POST /dev_layers` |
| `Raspberry/drifts/dashboard.html` | The dev view |
| `Raspberry/tests/test_facesignals.py` | 15 tests with synthetic landmarks (no camera) |

**Tokyo** can use the same dev view: `python -m tokyo run --face mesh`.
