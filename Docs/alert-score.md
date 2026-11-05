# The Alert Score

The ESP32 computes one **alert score**, 0–100, where **100 is fully alert**.
It sets the intervention level, drives the buzzer and motors, and is shown to
the driver on the Pi Zero's Whisplay HAT screen
([`../Hardware/whisplay-hat.md`](../Hardware/whisplay-hat.md)).

---

## The alert score

```
alert score = 0.70 × eye score  +  0.30 × driving score
```

| Part | Weight | Source |
| --- | --- | --- |
| **Eye score** | 70 % | The Pi camera. Once the eye-state model exists the Pi sends `EYE:<0-100>` (PERCLOS over a window). **Until then** its DROWSY / NOT_DROWSY answer is mapped: DROWSY → 10, NOT_DROWSY → 90, and an estimated value recovers toward 90 over about a minute |
| **Driving score** | 30 % | ESP32 sensors: steering reversals (gyro), harsh brakes (IMU), RPM / speed / throttle variation (OBD). Each signal costs **40 points at its trigger threshold** (up to 60 at 1.5×) |

| Alert score | Level | Whisplay screen | Whisplay RGB LED |
| --- | --- | --- | --- |
| 70–100 | OK | DRIFTS header, green bar | green |
| 50–69 | CAUTION | INTERVENTION 1, yellow | yellow |
| 0–49 | LOW | INTERVENTION 2, red | red, 1 s on / 1 s off |

**Hysteresis:** to move *up* a band the score must clear it by 5 points —
75 to return to OK, 55 to leave LOW. Moving down happens at the threshold. A
score sitting on a boundary therefore does not flicker the alerts.

**Partial score:** if no eye data has arrived for 2 minutes, the score uses
driving data only and `STATUS` says `PARTIAL`. Until the eye model exists this
is the normal state between Pi results.

> ⚠ **Until the eye model is trained, the score is a heuristic** built from
> driving data and the Pi's yes/no answer — not a measured fatigue level.
> Quote it that way in reports.

All weights and bands are in `Esp32/drifts_esp32/alertness.h` (`AlertConfig`).

---

## Bench test (ESP32 only, ~5 minutes)

Flash **v20**, then open the serial monitor
(`python -m serial.tools.miniterm --dtr 0 --rts 0 <port> 115200`). The
`[SCORE]` line from `STATUS` shows the score and level. With the Pi and its
Whisplay HAT running, the screen follows within a second.

| # | Type | Expect (`STATUS`, and the Whisplay screen) |
| --- | --- | --- |
| 1 | `STATUS` | A `[SCORE]` line: alert score, eye, driving, system issue |
| 2 | `SCORE 80` | `(OK)` · green |
| 3 | `SCORE 60` | `(CAUTION)` · INTERVENTION 1, yellow |
| 4 | `SCORE 30` | `(LOW)` · INTERVENTION 2, red |
| 5 | `SCORE 52` | **Still LOW**: leaving LOW needs 55 (hysteresis) |
| 6 | `SCORE 60` | `(CAUTION)` |
| 7 | `SCORE OFF` | Back to the live score |
| 8 | `DROWSY` | Motor and buzzer run, whatever the score |
| 9 | `STOP` | Alert ends |
| 10 | `EYE:30` | Eye 30 → alert ≈ 51 → CAUTION (calm driving) |
| 11 | `EYE:10` | Alert ≈ 37 → LOW |

`SCORE <n>` lasts 30 s, then the live score returns. With
`GATE_BENCH_MODE = true` (the current default), `STATUS` reports the
"bench mode" system issue; that is expected.

From the Pi or laptop test tool the same checks are option **E** in
`python -m drifts.alert_test` (`--serial <port>` or over BLE).

---

## Serial commands added

| Command | Does |
| --- | --- |
| `SCORE <0-100>` | Force the alert score for 30 s |
| `SCORE OFF` | End the override |
| `EYE:<0-100>` | Eye score, exactly as the Pi will send it (also accepted over BLE) |
| `DRV:<steer>:<throttle>:<brake>` | Reserved for the simulator source (below). Stored only |

Phone telemetry gains `"score"`, `"lvl"` (`OK` / `CAUTION` / `LOW`) and
`"part"` (partial score).

---

## Prepared: Logitech G29 simulator (not implemented)

The 30 % driving part is designed to accept a second source: a Logitech G29
wheel and pedals, for safe drowsy-driving trials in a simulator.

```
G29 ──USB──► laptop or Pi ──(USB serial or BLE)──► ESP32 ──► driving score
```

- **Message, reserved now:** `DRV:<steer_deg>:<throttle_pct>:<brake_pct>`,
  e.g. `DRV:-12.5:30:0`. The ESP32 already parses and stores it
  (`parseDrv` in `alertness.h`; host tests cover the EYE and DRV parsers).
- **Still to build:** a reader on the laptop / Pi (Python can
  read the G29 with `pygame`) streaming `DRV:` at ~20 Hz, and turning that
  stream into the same `DrivingRatios` the car sensors produce (steering
  reversals from wheel angle, brake and throttle behaviour from the pedals).

The score and the message format will not need to change when it lands.
