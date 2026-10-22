# Intervention Levels

> **Status: partly built.** The LCD shows Interventions 1–2; the outputs per
> level and Levels 3–4 are planned. This records the agreed direction so
> later edits stay consistent. Details are filled in when each level is built.

Each fatigue level maps to an **intervention**, escalating in priority.
**Interventions stack:** each level keeps everything below it running and adds
its own.

| Level | Driver state | Intervention | Hardware / software needed |
| --- | --- | --- | --- |
| **1** | Signs of fatigue | **Buzzer** | Have it (GPIO 11) |
| **2** | Worsening | Buzzer **+ seat vibration** (motor) | Have it (GPIO 10) |
| **3** | (TBD) | Buzzer + vibration **+ voice:** talk to the driver with standard text-to-speech | Speaker + TTS engine, likely on the Pi |
| **4** | (TBD) | All of the above **+ rest-stop alert:** direct the driver to the nearest gas station | Location + map lookup, likely the phone app |

## LCD

Line 1 shows the level (`INTERVENTION 1`, 14 of 16 characters). Line 2 rotates
every 2 s between the meaning and `Score NN/100`, plus `PULL OVER SAFELY` while
an alert runs. Details: [`lcd-display.md`](lcd-display.md).

| Level | Line 1 | Line 2 meaning | Status |
| --- | --- | --- | --- |
| 1 | `INTERVENTION 1` | `Signs of Fatigue` | Done |
| 2 | `INTERVENTION 2` | `Worsening Signs` | Done |
| 3 | `INTERVENTION 3` | proposed `Voice Check-In` | Planned |
| 4 | `INTERVENTION 4` | proposed `Find Rest Stop` | Planned |

## How this differs from today

- The firmware has **two** levels (score bands 50–69 / < 50). The LCD already
  shows them as `INTERVENTION 1` / `INTERVENTION 2`.
- Outputs are not yet split by level: Level 1 shows on the LCD and yellow LED
  but **does not sound the buzzer yet**, and on DROWSY the buzzer and motor
  fire together. Wiring outputs to levels is the next firmware step.

## To decide when built

- Score bands or triggers for Levels 3 and 4, e.g. a lower score, time spent at
  Level 2, or repeated alerts in a window.
- How a level ends: score recovers, driver acknowledges, or a timeout.
- Level 3: which TTS engine runs offline on the Pi Zero 2 W, which speaker, and
  the spoken phrases.
- Level 4: where location comes from (the Pi has no GPS; the phone does) and how
  the alert is shown.
- Confirm the proposed LCD text for Levels 3 and 4.
- Revisit the EU 2021/1341 / KSS reasoning in [`lcd-display.md`](lcd-display.md)
  for four levels instead of two.

Code to change when this lands: `alertness.h` (levels), `lcd_screen.h`
(titles and text), the alert/output logic in `drifts_esp32.ino`, and the Pi.
