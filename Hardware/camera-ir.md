# Driver Camera and IR Light (first model)

The first model (Pi Zero 2 W) uses the **Raspberry Pi Camera Module 3 NoIR**,
with 850 nm IR light so that it still sees the driver's face at night.

## Camera: Raspberry Pi Camera Module 3 NoIR

| | |
| --- | --- |
| Sensor | Sony IMX708, 12 MP; libcamera reports `imx708_noir` |
| Lens | standard 75° (not the Wide), **autofocus** 10 cm to infinity |
| NoIR | no IR-cut filter: it sees 850 nm light. In daylight colours look pink or purple; the face mesh doesn't mind |
| Cable | the Zero has the **narrow 22-pin** connector: use a Pi Zero camera cable (22-pin to 15-pin). The cable that comes with the camera does not fit the Zero |
| Software | the libcamera / picamera2 stack (Raspberry Pi OS Bookworm or later). Detected by `camera_auto_detect=1`, the default; no driver to install |

**Focus.** Autofocus can hunt in dim IR light, so DRIFTS holds the focus at
the driver's face. In `Raspberry/config.toml`:

```toml
[camera]
focus = "manual"        # or "continuous" / "auto" (one scan at start)
lens_position = 1.7     # dioptres = 1 / distance in metres: 1.7 = ~60 cm, 2.0 = 50 cm, 1.4 = 70 cm
```

**Where to put it.** 50–70 cm from the face, about eye level or a little
below, at most ~30° off to the side, with the steering wheel not covering the
eyes (on the dash behind the wheel, or at the base of the A-pillar).

**Check it:** `python -m drifts.camera_check --lcd` (below).

## IR light

At night the face needs light the driver can't see. The two boards on hand:

| | **DORHEA 3 W 850 nm board** (×2) | **ZORZA 12 V 850 nm, 48 LEDs** (×2) |
| --- | --- | --- |
| Made for | Raspberry Pi night-vision cameras, mounted next to the lens | CCTV cameras, lighting a yard |
| Size | small round board, one high-power LED | a ~48–52 mm board (typical for 48-LED 12 V boards) |
| Power | listed as DC 3–5 V, 500 mA–2 A (seller figures, not a datasheet) | **12 V** (we only have 5 V) |
| Switching | onboard light sensor turns it on in the dark; a small adjustable resistor (sellers disagree whether it sets the switching level or the brightness) | onboard light sensor (usually) |
| Range | a few metres | 15–20 m |
| For DRIFTS | **use this one** | **too big, too bright, wrong voltage.** The face is 60 cm away; it would need a 12 V boost converter or the car's 12 V. Keep it for the bench or Tokyo |

**Our mount (rev 8):** the IR board sits on the **other side of the LCD from the
camera, about 60 mm from the lens** (centre to centre), facing the driver, and is
powered from its own charger port ([schematic.md §5d](schematic.md)).

**How to fit the DORHEA board:**
- **Start with one board**, beside the camera and a few cm off the lens axis
  (straight-on light shows up as bright spots in glasses).
- **Power it before the Pi, not from the Pi.** Everything already runs through
  the Pi's micro-USB, which peaks near 2 A of its ~2.5 A rating
  ([schematic.md §5c](schematic.md)). A 3 W LED could add another 0.6–1 A. Feed
  it from the 5 V supply directly: a second port on the car charger, or a Y
  split ahead of the Pi.
- **The Camera Module 3 probably doesn't power it through the screw holes.**
  Some cheap night-vision camera boards do; the Pi's own cameras are not built
  for it. Measure between a mounting hole and GND: if there is no 3.3 V, wire
  the IR board's + and − to the 5 V feed.
- **Measure the current** with a USB meter at full light before it goes in the car.
- **Heat:** a 3 W LED gets hot. Give it a small heatsink, and keep it off plastic
  trim and the camera ribbon.
- **Eyes:** 850 nm is hard to see (a faint red glow), but it is still light
  going into the eyes for hours. Turn it down to the least that gives a clear
  face at night (`camera_check` prints the light level), and don't aim it
  straight into the eyes from close up. Production systems use low-power 940 nm
  LEDs flashed in step with the camera; this is a prototype.

**Later:** switch the board with a MOSFET from a free Pi GPIO (7, 9 or 16, see
[whisplay-hat.md](whisplay-hat.md)) so DRIFTS turns the IR on only when its
night detector says so, instead of the board's own light sensor.

## camera_check: the bring-up test

```bash
sudo systemctl stop drifts                  # it holds the camera and the screen
cd ~/drifts/Raspberry
.venv/bin/python -m drifts.camera_check            # 20 s, results in the terminal
.venv/bin/python -m drifts.camera_check --lcd      # the picture on the Whisplay LCD, to aim the camera
.venv/bin/python -m drifts.camera_check --lcd --seconds 120   # longer: try the IR board in the dark
```

| Check | What it means |
| --- | --- |
| Camera detected / NoIR | `imx708_noir`. None found: Pi off, re-seat the ribbon (contacts towards the board, both ends) |
| Face engine | **OpenCV** on the Pi Zero 2 W: MediaPipe 1.x's ARM build needs the CPU's AES instructions, which the Zero's Cortex-A53 doesn't have ("compiled with aes enabled ... Illegal instruction"). DRIFTS runs the same face models on OpenCV instead (`drifts/face_cv.py`), chosen automatically; `--face-engine opencv|mediapipe` forces one. It needs the official OpenCV wheel (`opencv-python-headless==4.10.0.84`, installed by `MESH=1`): Raspberry Pi OS's `python3-opencv` is built without TFLite support |
| Frame rate (with face mesh) | 5+ fps PASS, 2–5 WARN. This is the number for the Pi Zero face-mesh goal |
| Picture | mean light level: under 25 is dim (IR on at night), under 8 is black |
| Day / IR | grey picture = IR / night: the night detector switches DRIFTS' IR enhancement on |
| Face found | share of frames with a face: 80 %+ PASS. Low: re-aim (50–70 cm, eye level, ≤ 30°) |
| Focus / exposure | the lens position and exposure time. A long exposure (> 40 ms) means too little light: blinks blur |

The LCD view is mirrored like a mirror, with the face outline (eyes green
open, red closed), the frame rate, face found, the mesh time and the light
level. **Nothing is saved** except `camera_check_report.txt`; `--snapshot`
saves one frame, the only picture this tool writes.

Off the Pi (laptop): `python -m drifts.camera_check --video ../Simuletic_DMS_Dataset/images --lcd-sim`.
