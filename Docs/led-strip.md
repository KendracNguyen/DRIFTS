# LED Strip (WS2812B, 14 LEDs)

A 14-LED piece of the 2.7 mm ultra-narrow WS2812B strip sits above the LCD.
The first model (ESP32, firmware v17) and Tokyo (Pi 5) draw **identical
frames**: both are tested against the same table.

## What it shows

```
 [S][ 1  2  3  4  5  6  7  8  9 10 11 12 13 ]
  │  └──────────── score bar ───────────────┘
  status pixel
```

**Score bar** (pixels 1-13):

| Alert score | Colour | Bar | LEDs lit |
| --- | --- | --- | --- |
| 70-100 | Green | 75 % → 100 % | 10 → 13 |
| 40-69 | Yellow | 50 % → 74 % | 7 → 10 |
| 10-39 | Red | 25 % → 49 % | 3 → 6 |
| 0-9 | Red, **whole bar fast blink** (4 Hz) | 100 % | 13 |
| Drowsy alarm running | Red, **whole bar 1 s on / 1 s off**, in step with the buzzer | 100 % | 13 |
| Partial score (camera or driving data missing) | Same as above, **half brightness** | | |

Each colour band owns a quarter of the strip, so the bar never drops below
a quarter while there is a score, and the colour changes at the band edges.

| Score | 100 | 85 | 70 | 69 | 55 | 40 | 39 | 25 | 10 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LEDs | 13 G | 11 G | 10 G | 10 Y | 8 Y | 7 Y | 6 R | 5 R | 3 R |

**Status pixel** (pixel 0):

| Pattern | Meaning |
| --- | --- |
| Green, solid | Connected. First model: OBD dongle + Pi. Tokyo: camera + ESP32 driving data |
| **Red, slow blink (1 Hz)** | **Connection failure**: one of those links is lost |
| Yellow, slow blink | System issue. First model: bench mode or IMU missing. Tokyo: no driver profile |

A connection failure shows ahead of a system issue. At power-up the whole
strip does a white sweep for 1 s.

### Strip colours vs alert levels
The strip's bands (70 / 40 / 10) were chosen for the display. The **buzzer,
motors and LCD still use the alert levels**: Intervention 1 at 50-69 and
Intervention 2 below 50. So a score of 45 shows a yellow bar while
Intervention 2 is running. To make the strip follow the levels, set
`STRIP_YELLOW_MIN = 50` (`led_strip.h`) and `YELLOW_MIN = 50` (`Tokyo/tokyo/ledstrip.py`),
and update both tests.

## Wiring

**First model (ESP32-C6):**
```
 GPIO 23 ── 74AHCT125 1A        1Y ──[330 Ω]── strip DIN
 GND ────── 1OE                 VCC ── 5 V,  GND ── GND
 GND ────── 2A, 3A, 4A (unused inputs);  5 V ── 2OE, 3OE, 4OE
 5 V rail (LM2596) ──┬── strip 5V          GND ──┴── strip GND
              1000 µF at the strip
```
- The strip needs ≥ 3.5 V for a "1"; the C6 gives 3.3 V, hence the 74AHCT125.
  The BSS138 I2C shifter is too slow for the strip's 800 kHz data.
- 14 LEDs at the firmware's brightness (64/255) draw under 0.25 A, which the
  LM2596's 3 A covers.
- The firmware sends the frames with the C6's RMT peripheral. The bits are
  clocked out in hardware with interrupts left on, so Bluetooth isn't
  disturbed. A frame is sent only when it changes, at most 50 times a second.

**Tokyo (Pi 5):** GPIO 10 (SPI0 MOSI) through the same 74AHCT125 circuit.
See [`Tokyo/Docs/wiring.md`](../Tokyo/Docs/wiring.md#led-strip-gpio-10-ws2812b-14-leds).

## Testing
| | |
| --- | --- |
| ESP32, colours | `RAINBOW` (or `RAINBOW 5` for 5 rounds) flashes the whole strip red → orange → yellow → green → blue → indigo → violet, 0.5 s each, 3 rounds. Also accepted over BLE. `STOP` ends it. If red shows as green, the strip's colour order isn't GRB |
| ESP32 | `LEDSTRIP` in the serial monitor steps through 12 patterns, 1.5 s each, printing what each should look like. `STATUS` prints `[STRIP] on \| bar 11/13 LEDs` |
| Tokyo | `python -m tokyo test strip` (the same 12 steps) |
| Logic, no hardware | `cd Esp32/test && make`, `cd Tokyo && python -m pytest tests` |
