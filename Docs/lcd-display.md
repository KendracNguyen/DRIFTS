# LCD Display

A 16x2 character LCD shows a welcome message at power-up, then the live alert
score and the fatigue level.

| Part | Notes |
| --- | --- |
| HiLetgo HD44780 1602 LCD, 5 V, blue backlight | 16 columns × 2 rows |
| HiLetgo 2004/1602 IIC/I2C adapter (PCF8574) | Soldered to the LCD's 16-pin header |
| Bidirectional I2C level shifter (BSS138, 2-ch) | **Required** — the backpack is 5 V, the C6 is 3.3 V |

> **Mount it where a passenger or tester sees it, not the driver.** Like the
> LEDs, it is a status display. The motor and buzzer remain the driver's alert.

---

## What it shows

**Power-up** (2.5 s each):

```
┌────────────────┐     ┌────────────────┐
│   Welcome to   │ ──► │   Drive Safe   │ ──► live screen
│     DRIFTS     │     │                │
└────────────────┘     └────────────────┘
```

**Live screen** — level and score on line 1, what it means on line 2:

```
┌────────────────┐   ┌────────────────┐   ┌────────────────┐
│ALERT     87/100│   │LEVEL 1   62/100│   │LEVEL 2   38/100│
│   Drive Safe   │   │  Mild Fatigue  │   │Sev. Exhaustion │
└────────────────┘   └────────────────┘   └────────────────┘
   score 70-100         score 50-69          score 0-49
   LEDs: none           yellow solid         red fast blink
```

- **`*` after the score** (`42/100*`) = partial score: no eye data from the Pi
  in the last 2 minutes, so it is driving data only.
- **During the DROWSY alert** line 2 alternates every 1.5 s between the level
  and `PULL OVER SAFELY`. It never says "Drive Safe" while an alert runs.
- "Severe Exhaustion" is 17 characters; the display has 16, so it reads
  **Sev. Exhaustion**.
- Bands, hysteresis and the score itself are exactly the ones the LEDs use
  ([`status-leds.md`](status-leds.md)), so the screen and the LEDs never disagree.

---

## Why two levels, and why these bands

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
| — | Drive Safe | 70–100 | KSS ≤ 6 | — |
| **Level 1** | **Mild Fatigue** | 50–69 | KSS 7 — "may warn" | Yellow LED solid, screen only |
| **Level 2** | **Severe Exhaustion** | 0–49 | KSS 8+ — "must warn" | Red LED, and the motor/buzzer alert when the Pi confirms DROWSY |

> ⚠ The score-to-KSS column is a **design mapping**, not a validated
> measurement. Until the eye model is trained and checked against KSS
> self-ratings, describe the levels as "inspired by" the regulation in reports.

---

## Wiring

```
ESP32-C6 3V3  ── LV   level shifter   HV ── 5V      ── backpack VCC
ESP32-C6 GND  ── GND                 GND ── GND     ── backpack GND
GPIO 21 (SDA) ── LV1                 HV1 ───────────── backpack SDA
GPIO 22 (SCL) ── LV2                 HV2 ───────────── backpack SCL
```

The MPU-6050 stays on GPIO 21/22 on the 3.3 V side, as before. Diagram and
pin-by-pin table: [`../Hardware/pinout.md`](../Hardware/pinout.md). Cautions: [`../Hardware/schematic.md`](../Hardware/schematic.md) §5a.

**Why the level shifter.** At 5 V the PCF8574 needs ≥ 3.5 V (0.7 × VCC) to read
a logic high; the C6 drives 3.3 V. Many backpacks also pull SDA/SCL up to 5 V,
which the C6 pins are not rated for. Running the backpack at 3.3 V instead
leaves a 5 V LCD too dim to read and needs contrast changes.
([Arduino forum](https://forum.arduino.cc/t/3-3v-arduino-i2c-backpack-to-5v-lcd/411866),
[Universal-Solder backpack notes](https://universal-solder.ca/i2c-interface-with-pcf8574-for-lcd-1602-2004-etc/),
[ESP8266 forum](https://www.esp8266.com/viewtopic.php?p=85309))

**Why the bus is now 100 kHz.** The PCF8574 is rated to 100 kHz. The MPU-6050
shares the bus and was at 400 kHz; at 100 kHz an IMU read still takes under
2 ms, well inside the 10 ms sample period. `I2C_CLOCK_HZ` in `config.h`.

---

## Design decisions

| Decision | Why |
| --- | --- |
| Own small driver (`lcd_i2c.cpp`), no LiquidCrystal_I2C library | Libraries rewrite whole lines with blocking delays. Ours sends only changed characters, at most 4 per loop (~0.4 ms), so BLE and alert timing are unaffected on the single-core C6 |
| Address auto-detect 0x27 / 0x3F | Same board ships with PCF8574 (0x27) or PCF8574A (0x3F) |
| Firmware carries on with no LCD | A loose display must never stop alerts. Boot prints `[LCD] No display ...` |
| Screen content is pure code (`lcd_screen.h`) | Host tests check every screen is exactly 16 characters and says the right thing |
| Boot screens timed from the first loop | `setup()` blocks for a few seconds (IMU calibration, BLE); the welcome stays up meanwhile |
| Same bands as LEDs | One source of truth: `alertness.h` |

---

## Bench test (ESP32 only, ~5 minutes)

Flash **v11** or later and open the serial monitor
(`python -m serial.tools.miniterm --dtr 0 --rts 0 <port> 115200`).

| # | Do | Expect |
| --- | --- | --- |
| 1 | Reset the board | Boot log `[LCD] 16x2 display at 0x27, I2C 100 kHz`; screen **Welcome to / DRIFTS**, then **Drive Safe**, then the live screen |
| 2 | Blank or block characters | Turn the contrast pot on the backpack |
| 3 | `I2C SCAN` | `0x27 LCD backpack (PCF8574)` and `0x68 MPU-6050` |
| 4 | `STATUS` | `[SCORE] ... LCD on` and `[LCD] |...| |...|` matching the glass |
| 5 | `SCORE 80` | `ALERT     80/100` / `Drive Safe` |
| 6 | `SCORE 60` | `LEVEL 1   60/100` / `Mild Fatigue`, yellow LED solid |
| 7 | `SCORE 30` | `LEVEL 2   30/100` / `Sev. Exhaustion`, red LED blinking |
| 8 | `DROWSY` | Line 2 alternates with `PULL OVER SAFELY` |
| 9 | `STOP`, `SCORE OFF` | Back to the live score |
| 10 | Wiggle a wire, then `LCD` | Display re-initialises and redraws |

If the boot log says `No display at 0x27 or 0x3F`, run `I2C SCAN`. Nothing
found at all → SDA/SCL swapped or the shifter's LV/HV supplies missing. Only
0x68 → backpack power or its side of the shifter.

---

## Serial commands added

| Command | Does |
| --- | --- |
| `LCD` | Re-initialise the display and redraw (after a loose wire) |
| `I2C SCAN` | List every I2C device that answers |

Settings in `config.h`: `I2C_CLOCK_HZ`, `LCD_BACKLIGHT`, `LCD_REFRESH_MS`,
`LCD_CHARS_PER_LOOP`. Screen timings in `lcd_screen.h`.
