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

## Live line graphs (`live_graphs.py`)

Webcam (or clip) on the left with the major MediaPipe landmarks as colour-coded dots (jaw blue,
brows green, eyes magenta, nose bridge black, nose base yellow, outer lips cyan, inner lips red)
and the head's pitch / yaw / roll axes drawn from the nose tip; live line graphs on the right in
plain matplotlib style (white background, light grid, last 100 frames):

| Graph | Value | Axis reads |
| --- | --- | --- |
| Eye Opening (EAR) | mean EAR of both eyes, blue | Closed → Open |
| Nose Angle (NAR) | \|168–1\| / \|98–327\|, red (axis inverted: NAR rises as the head drops) | Down → Up |
| Mouth Opening (MAR) | MAR, green | Closed → Open |
| Yaw / Pitch / Roll | head pose in degrees, dashed blue | DRIFTS sign convention |
| Gaze | from the driver's seat in degrees (head yaw/pitch vs. your neutral + eyes); the windshield (±30° across, 10° up, 15° down) is the road range; red sight target | driver's left ↔ right, down ↔ up |

Each indicator records **on its own**: press its key to start, press again to stop; that graph
(PNG, 200 dpi) and its data (CSV) are saved to `captures/graphs-<timestamp>/`.

| Key | Action |
| --- | --- |
| `1` `2` `3` | record EAR / NAR / MAR |
| `4` | record head pose (yaw, pitch, roll) |
| `5` | record gaze |
| `a` | start / stop all |
| `c` | re-centre gaze (look straight at the road for 1 s) |
| `s` | snapshot of the whole window |
| `q` | quit (anything still recording is saved) |

```bash
python Tools/landmark_heatmap/live_graphs.py --camera 0
python Tools/landmark_heatmap/live_graphs.py --video clip.mp4 --record all --no-window
```
