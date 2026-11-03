# Tokyo Wiring (Raspberry Pi 5)

**No load connects straight to a Pi pin.** The Pi 5's GPIO pins are 3.3 V
and give only a few milliamps (up to ~12 mA), the same as the ESP32's. The
faster CPU doesn't change that. Motors, the buzzer coil and the LED strip each
need a driver part in between.

## Pin map

| Pi 5 pin (physical) | GPIO | Goes to | Through |
| --- | --- | --- | --- |
| 32 | **GPIO 12** | Vibration motors | MOSFET gate (bare motors) or module **IN** |
| 33 | **GPIO 13** | Buzzer | NPN transistor or MOSFET |
| 12 | **GPIO 18** | LED strip DIN | **74AHCT125** + 330 Ω |
| 3 | **GPIO 2** (SDA) | LCD backpack SDA | level shifter (LV1→HV1) |
| 5 | **GPIO 3** (SCL) | LCD backpack SCL | level shifter (LV2→HV2) |
| 1 | 3V3 | shifter LV, buzzer + | |
| 2 / 4 | 5V | motor +, shifter HV, LCD VCC, 74AHCT125 VCC | |
| 6, 9, 14… | GND | everything, **including the LED supply's GND** | |
| CAM/DISP 0 | — | Camera Module 3 NoIR | 22-pin "Standard–Mini" camera cable |

Add to `/boot/firmware/config.txt` (`install.sh` does this), then reboot:
```
dtparam=i2c_arm=on
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
```

---

## Vibration motors (GPIO 12)

**Which motors do you have?** Look at one:

| You see | Type | Wiring |
| --- | --- | --- |
| A **coin** with just **2 thin wires** (red + black/blue) | **Bare ERM motor** | Option A: needs a MOSFET |
| A **small board** with the motor on it and **3 pins** labelled **GND, VCC, IN** (or SIG) | **Driver module** | Option B: pin → IN directly |

**Option A: bare motors (all 4 in parallel)**
```
 5V ──────────┬───────────────┐
              │               │
          [ M1..M4 ]      1N5819 (stripe toward 5V)
              │               │
              └───────┬───────┘
                      │ drain
 GPIO 12 ──[100 Ω]── gate   AO3400A (logic-level N-MOSFET)
                      │ source
            10 kΩ ────┤ (gate to GND: keeps motors off during boot)
                      │
 GND ─────────────────┘
 + 220-470 µF across 5V-GND near the motors
```
They're 3 V motors on 5 V, so the software runs them at **60 % duty**
(`[motors] duty = 0.6`, about 3 V average) at 20 kHz.

**Option B: driver modules:** `GPIO 12 → IN`, `VCC → 5V` (or 3V3 if the
module says so), `GND → GND`. Set `duty = 1.0` unless the module's motor needs
less.

## Buzzer (GPIO 13)

The TMB12A03 is an **active magnetic buzzer** (3 V, ~30 mA, a coil): it beeps
by itself when powered, so the Pi only switches it on and off
(`[buzzer] kind = "active"`).
```
 3V3 ───┬──────────── buzzer (+)
        │             buzzer (−) ──┐
     1N4148 (stripe to 3V3)        │ collector
        └──────────────────────────┤
 GPIO 13 ──[1 kΩ]── base   2N3904 / 2N2222 (NPN)
                                   │ emitter
 GND ──────────────────────────────┘
```
A passive piezo (`kind = "passive"`) gets a 2.7 kHz tone from hardware PWM
instead: GPIO 13 → 100 Ω → piezo → GND.

## LED strip (GPIO 18): WS2812B, 160 LEDs, 3.2 ft

```
                 74AHCT125 (powered from 5 V)
 GPIO 18 ───────── 1A          1Y ──[330 Ω]── DIN  (arrow on the strip points away)
 GND ───────────── 1OE (tie low = enabled)
 5V ────────────── VCC     GND ── GND

 5 V LED supply (+) ──┬── strip 5V (start)  ── and again at the far end
                      │
                 1000 µF (≥ 6.3 V) across + / − at the strip input
 5 V LED supply (−) ──┴── strip GND ── Pi GND   (common ground is required)
```
- **Why the 74AHCT125:** the strip needs about 3.5 V for a "1" on its 5 V
  data input, and the Pi gives 3.3 V: marginal, and it flickers. The
  BSS138 I2C level shifter is too slow for the strip's 800 kHz signal.
- **Power:** at full white, 160 LEDs draw ~9.6 A. The software caps it:
  `brightness = 0.25` and `max_current_a = 1.5`, so frames are dimmed to stay
  under 1.5 A. Use a **separate 5 V 3 A supply**, not the Pi's 5 V pin.
- **Power injection:** this strip is only 2.7 mm wide, with thin copper. Feed
  5 V at **both ends** so the far LEDs don't turn orange or brown.

## LCD (GPIO 2 / 3)
The same as the first model ([`../../Hardware/pinout.md`](../../Hardware/pinout.md)):
through the BSS138 level shifter, or the all-3.3 V hookup until you have one.
**Never connect a 5 V-powered backpack's SDA/SCL directly to the Pi.** The Pi
5 already has 1.8 kΩ pull-ups to 3.3 V on GPIO 2/3.

## Camera Module 3 NoIR (arrives 11/8)
- The Pi 5 has 22-pin camera connectors. Use the **22-to-15-pin "Standard–Mini"
  cable**, and check whether one came in the box.
- **NoIR has no IR LEDs.** For night, add an **850 nm** (faint red glow) or
  **940 nm** (invisible) IR illuminator aimed at the face, e.g. the IR LED
  boards from the Arducam module.
- Daylight colours look pink without the IR filter; face tracking copes.
- Test: `rpicam-hello -t 5000`.

## Power summary
| Load | Supply |
| --- | --- |
| Pi 5 | Official 27 W USB-C supply |
| Motors (≤ ~0.5 A), buzzer, LCD, 74AHCT125 | Pi 5V / 3V3 pins (with the 27 W supply) |
| LED strip | **Separate 5 V 3 A**, grounds joined |
