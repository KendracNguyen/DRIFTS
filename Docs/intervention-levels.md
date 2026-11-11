# Intervention Levels

> **Status: partly built.** Levels 1–2 are shown on the driver screen (the
> Whisplay HAT on the Pi Zero) and spoken; the outputs per level and Levels 3–4
> are planned. This records the agreed direction so later edits stay consistent.

Each fatigue level maps to an **intervention**, escalating in priority.
**Interventions stack:** each level keeps everything below it running and adds
its own.

| Level | Driver state | Intervention | Hardware / software needed |
| --- | --- | --- | --- |
| **1** | Signs of fatigue | **Buzzer** | Have it (GPIO 11) |
| **2** | Worsening | Buzzer **+ seat vibration** (motor) | Have it (GPIO 10) |
| **3** | (TBD) | Buzzer + vibration **+ voice check-in:** ask the driver to confirm with the button | Whisplay HAT speaker + button (have it) |
| **4** | (TBD) | All of the above **+ rest-stop alert:** direct the driver to the nearest gas station | Location + map lookup, likely the phone app |

## Driver screen

The Whisplay HAT's colour screen shows the alert score in a ring gauge,
coloured by the level, with a status label under it
([`../Hardware/whisplay-hat.md`](../Hardware/whisplay-hat.md)):

| Level | Ring and label | Text under it | Status |
| --- | --- | --- | --- |
| OK | green, `ALERTNESS NORMAL` | eyes and pose (or driving score) | Done |
| 1 | yellow, `CAUTION` | blink rate and yaw (or driving score and speed) | Done |
| 2 | red, `HIGH FATIGUE` | "Intervention active · Audio + vibration" | Done |
| 3 | proposed: the emergency screen, "Are you okay? Press the button" | | Planned |
| 4 | proposed: the rest-stop screen | | Planned |

## Why these two bands

Commercial driver drowsiness systems warn in **two steps**, and EU law
describes them the same way.

- **EU Regulation 2021/1341** (driver drowsiness and attention warning, DDAW,
  mandatory on new EU cars since 2024), Annex I §3.3.1: the system *shall*
  warn at a drowsiness level equivalent to **8 or above on the Karolinska
  Sleepiness Scale (KSS)**, and *may* warn at **level 7**.
  ([regulation text](https://lexaris.de/book/version/documentflat/head/2056993))
- KSS 7 = "sleepy, but no effort to keep awake"; KSS 8 = "sleepy, some effort
  to keep awake"; KSS 9 = "very sleepy, great effort to keep awake".
- Optalert, a commercial eye-based system, likewise reports a continuous
  drowsiness score with a moderate and a high-risk band
  ([Optalert](https://www.optalert.com/?p=1750)).
- PERCLOS-based studies also split drowsiness into moderate and severe, though
  the exact cut-offs vary between studies
  ([Univ. of Iowa driving assessment](https://pubs.lib.uiowa.edu/driving/article/27959/galley/136251/view)).

So:

| Level | Text | Alert score | Comparable to | Output |
| --- | --- | --- | --- | --- |
| — | Alertness normal | 70–100 | KSS ≤ 6 | — |
| **Intervention 1** | **Signs of Fatigue** | 50–69 | KSS 7 — "may warn" | Screen and RGB LED yellow. Planned: buzzer |
| **Intervention 2** | **Worsening Signs** | 0–49 | KSS 8+ — "must warn" | Screen and RGB LED red; **buzzer (1 s on / 1 s off) + motors as soon as the score is below 50** (firmware v24), or when the Pi reports DROWSY. Stops when the score recovers to 55 |

> ⚠ The score-to-KSS column is a **design mapping**, not a validated
> measurement. Until the eye model is trained and checked against KSS
> self-ratings, describe the levels as "inspired by" the regulation in reports.

---

## How this differs from today

- The firmware has **two** levels (score bands 50–69 / < 50). The driver
  screen shows them as `INTERVENTION 1` / `INTERVENTION 2`.
- Outputs are not yet split by level: Level 1 shows on the screen and RGB
  LED but **does not sound the buzzer yet**, and on DROWSY the buzzer and motor
  fire together. Wiring outputs to levels is the next firmware step.

## To decide when built

- Score bands or triggers for Levels 3 and 4, e.g. a lower score, time spent at
  Level 2, or repeated alerts in a window.
- How a level ends: score recovers, driver acknowledges, or a timeout.
- Level 3: **voice decided (Nov 2026)**: Piper clips on the Whisplay HAT's
  speaker (first model) and a small USB speaker on Tokyo ([voice.md](voice.md)).
  Levels 1 and 2 are already spoken, and the Whisplay button already
  acknowledges an alert. Still open: the check-in itself (clip 9, "Are you
  okay? Press the button to confirm") and what happens when there's no answer.
- Level 4: where location comes from (the Pi has no GPS; the phone does) and how
  the alert is shown.
- Confirm the proposed screen text for Levels 3 and 4.
- Revisit the EU 2021/1341 / KSS reasoning above for four levels instead of two.

Code to change when this lands: `alertness.h` (levels), the alert/output logic
in `drifts_esp32.ino`, and the Pi's `drifts/whisplay_ui.py` (screens, voice, button).
