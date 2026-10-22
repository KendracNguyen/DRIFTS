# Intervention Levels (planned)

> **Status: planned, not implemented.** This records the agreed direction so
> later edits stay consistent. Details are filled in when each level is built.

Each fatigue level maps to an **intervention**, escalating in priority:

| Level | Driver state | Intervention | Hardware / software needed |
| --- | --- | --- | --- |
| **1** | Signs of fatigue | **Buzzer** | Have it (GPIO 11) |
| **2** | Worsening | **Seat vibration** (motor) | Have it (GPIO 10) |
| **3** | (TBD) | **Voice:** talk to the driver with standard text-to-speech | Speaker + TTS engine, likely on the Pi |
| **4** | (TBD) | **Rest-stop alert:** direct the driver to the nearest gas station | Location + map lookup, likely the phone app |

## How this differs from today (v11)

- The firmware has **two** levels (score bands 50–69 / < 50), and the LCD shows
  `LEVEL 1` / `LEVEL 2` from those bands.
- On DROWSY, the buzzer **and** motor fire together. Under this plan the buzzer
  alone is Level 1 and the motor is added at Level 2.

## To decide when built

- Score bands or triggers for Levels 3 and 4, e.g. a lower score, time spent at
  Level 2, or repeated alerts in a window.
- Whether levels stack (Level 2 = buzzer + motor) or replace each other.
- How a level ends: score recovers, driver acknowledges, or a timeout.
- Level 3: which TTS engine runs offline on the Pi Zero 2 W, which speaker, and
  the spoken phrases.
- Level 4: where location comes from (the Pi has no GPS; the phone does) and how
  the alert is shown.
- LCD text for Levels 3 and 4 (16 characters per line).
- Revisit the EU 2021/1341 / KSS reasoning in [`lcd-display.md`](lcd-display.md)
  for four levels instead of two.

Code to change when this lands: `alertness.h` (levels), `lcd_screen.h`
(titles and text), the alert/output logic in `drifts_esp32.ino`, and the Pi.
