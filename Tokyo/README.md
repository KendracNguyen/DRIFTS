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
| **1** | **Outputs:** buzzer, motors, LED strip, LCD, stacking interventions, hardware test tool | **This PR** (tested in sim; hardware untested) |
| 2 | **Camera + face:** Camera Module 3 NoIR (arrives 11/8) with the Pi 5 face pipeline from the two-rig demo (dms-face + mouth/yawn + night) | Next |
| 3 | **OBD over the Pi 5's Bluetooth:** port of the ESP32 dongle logic to Python | Planned |
| 4 | **Alert score + app:** port of `alertness.h`, the web dashboard, interventions from the live score | Planned |
| 5 | Levels 3-4: voice check-in, rest-stop routing | Later |

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
tokyo/__main__.py       test / demo tool
tests/test_tokyo.py     14 tests, no hardware
```

The LCD screens were checked against the first model's C++ (`lcd_screen.h`):
3,240 combinations of time, score, level and alarm give identical text.
