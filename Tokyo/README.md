# Tokyo: DRIFTS on a single Raspberry Pi 5

The second DRIFTS project. One **Raspberry Pi 5** does everything the first
model splits between an **ESP32-C6** and a **Pi Zero 2 W**. The first model
stays as it is (`Esp32/`, `Raspberry/`).

| | First model | **Tokyo** |
| --- | --- | --- |
| Compute | ESP32-C6 + Pi Zero 2 W | **Raspberry Pi 5** |
| Camera | Pi camera / AI Camera | **Camera Module 3 NoIR** (+ IR illuminator) |
| Face signals | 2-4 of 8 (Zero) | **8 of 8** (MediaPipe, via dms-face) |
| Alert outputs | ESP32 GPIO | **Pi 5 GPIO**: buzzer, 4 vibration motors |
| Status light | 3 LEDs | **160-LED WS2812B strip** above the LCD |
| LCD | ESP32 I2C | **Pi 5 I2C**, same screens |
| OBD | ESP32 BLE central | **Pi 5 Bluetooth** directly |

## Roadmap

| Phase | What | Status |
| --- | --- | --- |
| 1 | **Outputs:** buzzer, motors, LED strip, LCD, stacking interventions, hardware test tool | Done (sim-tested) |
| **2** | **Camera + face:** dms-face (MediaPipe, 478 landmarks) + mouth/yawn + night, from the two-rig demo | **Done** (laptop / stand-in tested; NoIR arrives 11/8) |
| 3 | OBD over the Pi 5's own Bluetooth | **Skipped for now:** the first model's ESP32 keeps reading the dongle and shares its driving score |
| **4** | **Alert score + app:** 0.7 × camera eye + 0.3 × ESP32 driving → levels → outputs, web dashboard | **Done** |
| 5 | Levels 3-4: voice check-in, rest-stop routing | Later |

## How the pieces connect

```
 Tokyo (Raspberry Pi 5)                                   First model (unchanged)
 ─────────────────────                                    ─────────────────────────
 Camera Module 3 NoIR ─► dms-face (MediaPipe)             OBD dongle ─BLE─► ESP32-C6 ◄─BLE─ Pi Zero 2 W
   EAR · blinks · PERCLOS · iris gaze · head pose          (driving score: steering,        (its Pi)
   + mouth / yawn · night (IR)                              brakes, RPM/speed/throttle)
        │ eye score = 100 × (1 − PERCLOS)                        │
        ▼                                                        │ BLE telemetry "dscore"
   alert score = 0.7 × eye + 0.3 × driving  ◄────────────────────┘ (listen only: Tokyo never writes)
        │
        ▼  level (hysteresis, 3 s hold; dms-face DROWSY forces Level 2)
   buzzer · 4 motors · 160-LED strip · 16x2 LCD · web dashboard (:8080)
```

- The ESP32 needs **firmware v16** to share its driving score over Bluetooth
  (one new JSON field). Over USB (`--esp serial`) any recent firmware works.
- Without ESP32 data the score uses the camera alone and the dashboard says
  "eye only". Without a face the score uses driving only, flagged partial, as
  in the first model.

## Running it

### On the laptop (now, until the NoIR arrives)
```powershell
cd C:\Users\Kendra\Documents\School\Senior
git clone https://github.com/youssefmedhat4/dms-face
cd dms-face; git checkout 7b32c63
pip install --no-deps "mediapipe>=1.0.0"; pip install -r requirements-lean.txt "opencv-python<5" bleak pyserial
python run.py --calibrate --profile kendra          # look at the screen, then close your eyes when asked
$env:DMS_FACE_DIR = "$PWD"

cd ..\DRIFTS\Tokyo
python -m tokyo run --profile kendra --camera 0 --mirror --esp sim
```
Open http://localhost:8080. Off the Pi 5 the buzzer, motors, strip and LCD
are **simulated**: the dashboard shows what they would do (intervention pills,
LCD text). Variations:
- `--esp serial --port COM5`: driving score from the real ESP32 over USB.
- `--video clip.mp4`: a recorded drive instead of the webcam.

### On the Pi 5 (with the NoIR camera)
```bash
cd ~/drifts/Tokyo && bash install.sh && sudo reboot
.venv/bin/python -m tokyo test all                       # outputs, one by one
cd ~/dms-face && ~/drifts/Tokyo/.venv/bin/python run.py --calibrate --profile kendra --no-display
cd ~/drifts/Tokyo && .venv/bin/python -m tokyo run --profile kendra
```
The ESP32 (v16) and its Pi Zero run as usual; Tokyo listens to the ESP32's
telemetry over Bluetooth. Turn your head right once: yaw should go positive
(else `--yaw-sign -1`).

## Interventions (stacking, as agreed)

| Level | When | Outputs |
| --- | --- | --- |
| 1 | score < 70 (signs of fatigue) | buzzer **1 s beep / 1 s pause**; strip bar amber; LCD `INTERVENTION 1` |
| 2 | score < 50 (worsening) or confirmed drowsy | + **seat vibration** 0.5 s / 0.3 s; strip flashes red with the buzzer; LCD `INTERVENTION 2` |

Leaving a level needs 5 points of margin and at least 3 s at that level, so
a score on a boundary does not stutter the outputs.

## Setup on the Pi 5

Wiring first: [`Docs/wiring.md`](Docs/wiring.md).

```bash
cd ~/drifts/Tokyo && bash install.sh        # I2C, PWM overlay, venv, libraries; then reboot
.venv/bin/python -m tokyo test buzzer
.venv/bin/python -m tokyo test motors
.venv/bin/python -m tokyo test strip
.venv/bin/python -m tokyo test lcd
.venv/bin/python -m tokyo demo              # score 90 -> 60 -> 35 -> 85, all outputs together
```
Every command also runs on a laptop with `--sim`, printing what each output
would do (the strip as a text bar).

Settings (pins, motor duty, buzzer type, strip brightness and current cap,
level bands) are in `tokyo/config.py`; override any of them in `tokyo.toml`.

## Code

```
tokyo/config.py         pin map + settings, tokyo.toml overrides
tokyo/outputs.py        buzzer (active / passive), motors (kernel hardware PWM, gpiozero fallback)
tokyo/ledstrip.py       160-LED status + alert-score bar, brightness / current cap, Pi 5 PIO backend
tokyo/lcd.py            HD44780 + PCF8574 over smbus2; screens identical to the first model
tokyo/interventions.py  score -> level (hysteresis, hold) -> buzzer / motor patterns
tokyo/score.py          alert score: 0.7 eye + 0.3 driving, with explicit fallbacks
tokyo/app.py            python -m tokyo run: face pipeline + score + outputs + dashboard
tokyo/__main__.py       test / demo / run
tests/                  21 tests, no hardware
```

The LCD screens were checked against the first model's C++ (`lcd_screen.h`):
3,240 combinations of time, score, level and alarm give identical text.
