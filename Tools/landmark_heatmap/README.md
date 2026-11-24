# Landmark heat maps and close-ups (dev only)

**Laptop only.** Not part of the Pi app: `deploy.ps1` copies only `Raspberry/`
and `Voice/`, and the script refuses to run on a Raspberry Pi.

Runs MediaPipe's Face Landmarker (478 points, irises included) on a photo, a
clip or the webcam, and writes an in-depth picture of every facial landmark.

```bash
pip install -r Tools/landmark_heatmap/requirements-dev.txt
python Tools/landmark_heatmap/landmark_heatmap.py --image face.jpg
python Tools/landmark_heatmap/landmark_heatmap.py --camera 0 --seconds 5 --preview   # blink, yawn, nod
python Tools/landmark_heatmap/landmark_heatmap.py --video clip.mp4 --tiles
```

Output goes to `Tools/landmark_heatmap/captures/<timestamp>/` (git-ignored: faces).

| File | What it shows |
| --- | --- |
| `depth_heatmap.png` | Depth of the face from the landmarks' z, interpolated, with contours (near = red) |
| `signal_heatmap.png` | Where DRIFTS looks: landmarks the 8 signals use, weighted by `facesignals.WEIGHTS` |
| `motion_heatmap.png` | Clip / webcam only: how much each landmark moved, head motion removed |
| `regions/*.png`, `regions_sheet.png` | Close-up of each region (eyes, irises, brows, lips, nose, face oval), every landmark numbered, EAR / MAR drawn |
| `points_sheet.png` | One zoomed tile for each of the 478 landmarks, grouped and colour-coded by region |
| `points/NNN.png` | With `--tiles`: each landmark tile as its own file |
| `blendshapes.png` | MediaPipe's 52 expression scores (eyeBlink, jawOpen, eyeLook…) |
| `landmarks.csv` | index, region, x, y, z (and motion) for all 478 points |
| `summary.json` | EAR (both eyes), MAR, head pose (DRIFTS sign convention), top blendshapes |

**For sharp close-ups:** use a high-resolution, evenly lit, front-on photo with the face
filling the frame. The tiles are crops of the original pixels; the script warns when the
eye spacing is under 60 px. `--camera` asks for 1920×1080 (`--width/--height`).

Landmark indices, EAR / MAR points and weights come from `Raspberry/drifts/facesignals.py`
and `mouth.py`, so these images always match what the Pi measures.
