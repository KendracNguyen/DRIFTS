# Status LEDs and the Alert Score

Three LEDs on the ESP32 show connections, caution, and drowsiness at a
glance. They are driven by the **alert score**: 0–100, where **100 is fully
alert**.

> **Mount the LEDs where a passenger or tester sees them, not the driver.**
> Flashing lights in the driver's view are a distraction, especially at night.
> The motor and buzzer remain the driver's alert. Brightness is
> `LED_BRIGHTNESS` in `config.h` (default 255 = full); lower it for night tests.

---

## What the LEDs mean

| LED | GPIO | Off | Slow blink (1 Hz) | Solid | Fast blink (4 Hz) |
| --- | --- | --- | --- | --- | --- |
| 🟢 **Green** — connections | 2 | Powered off | A link is missing: dongle not polling, or Pi not connected | Dongle polling **and** Pi connected | — |
| 🟡 **Yellow** — caution / system | 3 | All fine | **System issue:** bench mode on, or IMU missing | **Caution:** alert score 50–69 | — |
| 🔴 **Red** — drowsiness | 4 | Not drowsy | — | — | **Drowsy:** alert score below 50, or the DROWSY alert is running |

- **Power-up:** all three light for 1 s (lamp test). A dark LED here is a wiring fault.
- **Caution wins yellow.** If the score is 50–69 *and* there is a system issue,
  yellow shows solid. Bench mode is on during all desk testing; if it won,
  caution would never be visible. System issues are always in `STATUS`.
- **Every flashing red means drowsiness.** Nothing else uses red.
- Nothing blinks faster than 4 Hz.

### Quick reference

| You see | Meaning |
| --- | --- |
| 🟢 solid, others off | All good |
| 🟢 blinking | Check the dongle (ignition on?) and the Pi |
| 🟡 solid | Getting tired — alert score 50–69 |
| 🟡 blinking | System issue — run `STATUS` |
| 🔴 blinking | Drowsy |

---

## The alert score

```
alert score = 0.70 × eye score  +  0.30 × driving score
```

| Part | Weight | Source |
| --- | --- | --- |
| **Eye score** | 70 % | The Pi camera. Once the eye-state model exists the Pi sends `EYE:<0-100>` (PERCLOS over a window). **Until then** its DROWSY / NOT_DROWSY answer is mapped: DROWSY → 10, NOT_DROWSY → 90, and an estimated value recovers toward 90 over about a minute |
| **Driving score** | 30 % | ESP32 sensors: steering reversals (gyro), harsh brakes (IMU), RPM / speed / throttle variation (OBD). Each signal costs **40 points at its trigger threshold** (up to 60 at 1.5×) |

| Alert score | Level | LEDs |
| --- | --- | --- |
| 70–100 | OK | red and yellow off |
| 50–69 | CAUTION | yellow solid |
| 0–49 | LOW | red fast blink |

**Hysteresis:** to move *up* a band the score must clear it by 5 points —
75 to return to OK, 55 to leave LOW. Moving down happens at the threshold. A
score sitting on a boundary therefore does not flicker the LEDs.

**Partial score:** if no eye data has arrived for 2 minutes, the score uses
driving data only and `STATUS` says `PARTIAL`. Until the eye model exists this
is the normal state between Pi results.

> ⚠ **Until the eye model is trained, the score is a heuristic** built from
> driving data and the Pi's yes/no answer — not a measured fatigue level.
> Quote it that way in reports.

All weights and bands are in `Esp32/drifts_esp32/alertness.h` (`AlertConfig`).

---

## Wiring

```
   GPIO 2 ───[ 100 Ω ]───▶|─── GND     GREEN  (330 Ω if yellow-green)
   GPIO 3 ───[ 330 Ω ]───▶|─── GND     YELLOW
   GPIO 4 ───[ 330 Ω ]───▶|─── GND     RED
```

Long leg (anode) toward the resistor. Details and the GPIO 4 caution:
[`Hardware/schematic.md`](../Hardware/schematic.md) §4.

---

## Bench test (ESP32 only, ~5 minutes)

Flash **v10** or later, then open the serial monitor
(`python -m serial.tools.miniterm --dtr 0 --rts 0 <port> 115200`).

| # | Type | Expect |
| --- | --- | --- |
| 1 | *(reset the board)* | All three LEDs on for 1 s |
| 2 | `LEDS` | Green, then yellow, then red, each: solid → slow → fast (2 s each, 18 s total) |
| 3 | `STATUS` | A `[SCORE]` line: alert score, eye, driving, LED states, system issue |
| 4 | `SCORE 80` | Red and yellow off |
| 5 | `SCORE 60` | **Yellow solid** |
| 6 | `SCORE 30` | **Red fast blink** |
| 7 | `SCORE 52` | **Still red** — leaving LOW needs 55 (hysteresis) |
| 7b | `SCORE 60` | Yellow solid |
| 8 | `SCORE OFF` | Back to the live score |
| 9 | `DROWSY` | Red fast blink (and motor/buzzer), whatever the score |
| 10 | `STOP` | Red off once the score recovers |
| 11 | `EYE:30` | Eye 30 → alert ≈ 51 → **yellow solid** (calm driving) |
| 12 | `EYE:10` | Alert ≈ 37 → **red fast blink** |

With `GATE_BENCH_MODE = true` (the current default), yellow blinks whenever
the score is OK — that is the "bench mode" system issue, and is expected.

`SCORE <n>` lasts 30 s, then the live score returns.

From the Pi or laptop test tool the same checks are option **E** in
`python -m drifts.alert_test` (`--serial <port>` or over BLE).

---

## Serial commands added

| Command | Does |
| --- | --- |
| `LEDS` | Cycle every pattern on every LED (wiring check) |
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
- **Still to build, after the LCD:** a reader on the laptop / Pi (Python can
  read the G29 with `pygame`) streaming `DRV:` at ~20 Hz, and turning that
  stream into the same `DrivingRatios` the car sensors produce (steering
  reversals from wheel angle, brake and throttle behaviour from the pedals).

The score, the LEDs and the message format will not need to change when it lands.
