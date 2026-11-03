# ESP32-C6 Pinout (first model)

Every wire to the ESP32-C6, as of **firmware v18** and **schematic rev 5**:
status LEDs, I2C LCD + MPU-6050, differential piezo, vibration motors,
**WS2812B LED strip** and **DFPlayer Mini voice**.

![DRIFTS ESP32-C6 pinout](pinout.svg)

Whole-system drawing: [`drawings/DRIFTS_Schematic_rev5.pdf`](drawings/DRIFTS_Schematic_rev5.pdf)
(PNG alongside). Both drawings are generated: `python Hardware/drawings/draw_schematic.py`
and `python Hardware/drawings/draw_pinout.py`.

Find pins by the **label printed on the board** (`5V`, `3V3`, `G`/`GND`, `2`,
`3`, `4`, `10`, `11`, `18`, `19`, `20`, `21`, `22`, `23`), not by position.
Clone boards with the CH343 USB chip place pins differently from the Espressif DevKitC.

---

## Pin table

| ESP32-C6 pin | Goes to | Through | Firmware constant |
| --- | --- | --- | --- |
| **5V** | LCD backpack VCC, BSS138 shifter HV, 74AHCT125 VCC (pin 14) and 2OE–4OE, LED strip 5V, DFPlayer VCC | — | — |
| **3V3** | MPU-6050 VCC, BSS138 shifter LV, motor module VCC | — | — |
| **GND** | Everything: one common ground | — | — |
| **GPIO 2** | Green LED anode → cathode to GND | R5 100 Ω (330 Ω if yellow-green) | `LED_GREEN_PIN` |
| **GPIO 3** | Yellow LED anode → cathode to GND | R8 330 Ω | `LED_YELLOW_PIN` |
| **GPIO 4** | Red LED anode → cathode to GND | R9 330 Ω | `LED_RED_PIN`. LED only: strapping pin |
| **GPIO 10** | **IN** of all 4 vibration motor modules | R1 100 Ω; R2 10 kΩ IN-to-GND | `MOTOR_PIN` |
| **GPIO 11** | Piezo LS1 pin 1 | R6 100 Ω | `PIEZO_A_PIN` |
| **GPIO 18** | Piezo LS1 pin 2 (neither leg to GND) | R7 100 Ω | `PIEZO_B_PIN`: the same signal as 11, inverted |
| **GPIO 19** | DFPlayer Mini **RX** (pin 2) | **R11 1 kΩ** | `VOICE_TX_PIN` (UART1 TX) |
| **GPIO 20** | DFPlayer Mini **TX** (pin 3) | — | `VOICE_RX_PIN` (UART1 RX) |
| **GPIO 21** | MPU-6050 SDA **and** BSS138 shifter LV1 | — | `IMU_SDA_PIN` |
| **GPIO 22** | MPU-6050 SCL **and** BSS138 shifter LV2 | — | `IMU_SCL_PIN` |
| **GPIO 23** | 74AHCT125 **1A** (pin 2) → LED strip DIN | 74AHCT125 + R10 330 Ω | `STRIP_PIN` |

`python3 Hardware/check_pins.py` checks this table (from `pinout.csv`) against
`config.h` and the reserved-pin list. It must print `OK: 11 pins agree`.

---

## LCD + MPU-6050: BSS138 level shifter (U7)

Level shifter pin names vary. SparkFun BOB-12009 uses **LV/LV1–4, HV/HV1–4**;
Adafruit 757 uses **LV/A1–4, HV/B1–4**. The low side is the 3.3 V side.

| From | To | Wire |
| --- | --- | --- |
| ESP32 **3V3** | Shifter **LV** | power, low side |
| ESP32 **5V** | Shifter **HV** | power, high side |
| ESP32 **GND** | Shifter **GND** (both sides if it has two) | ground |
| ESP32 **GPIO 21** | Shifter **LV1** (A1) | SDA, 3.3 V |
| ESP32 **GPIO 22** | Shifter **LV2** (A2) | SCL, 3.3 V |
| Shifter **HV1** (B1) | Backpack **SDA** | SDA, 5 V |
| Shifter **HV2** (B2) | Backpack **SCL** | SCL, 5 V |
| ESP32 **5V** | Backpack **VCC** | power |
| ESP32 **GND** | Backpack **GND** | ground |
| MPU-6050 **SDA / SCL** | GPIO 21 / GPIO 22 (low side) | |
| MPU-6050 **VCC / GND** | 3V3 / GND | AD0 to GND = address 0x68 |

> ⚠ **Never connect the backpack's SDA/SCL directly to GPIO 21/22.** The
> backpack pulls them up to 5 V; the C6 pins are 3.3 V only.

Keep the backpack's backlight jumper in place. If the backlight is on but there
is no text, turn the contrast potentiometer. Test: `I2C SCAN` lists `0x27`
(LCD) and `0x68` (MPU-6050).

---

## LED strip: 74AHCT125 buffer (U9) + WS2812B ×14

The strip needs at least 3.5 V for a data "1", and the C6 gives 3.3 V. The
74AHCT125 runs from 5 V and takes 3.3 V in. The BSS138 shifter is too slow
for the strip's 800 kHz signal.

| 74AHCT125 pin | Name | Connect to |
| --- | --- | --- |
| 1 | 1OE | **GND** (turns gate 1 on) |
| 2 | 1A | **ESP32 GPIO 23** |
| 3 | 1Y | **R10 330 Ω** → strip **DIN** |
| 4 | 2OE | **5V** (unused gate off) |
| 5 | 2A | **GND** |
| 6 | 2Y | not connected |
| 7 | GND | **GND** |
| 8 | 3Y | not connected |
| 9 | 3A | **GND** |
| 10 | 3OE | **5V** |
| 11 | 4Y | not connected |
| 12 | 4A | **GND** |
| 13 | 4OE | **5V** |
| 14 | VCC | **5V** |

Pin 1 is next to the notch or dot on the chip. Pins 1–7 run down one side and
8–14 back up the other.

| Strip | Connect to |
| --- | --- |
| **DIN** (the end the arrows point away from) | R10 330 Ω from 74AHCT125 pin 3 |
| **5V** | 5 V rail |
| **GND** | GND, common with the ESP32 |
| **C5 1000 µF** (≥ 6.3 V) | across 5V / GND at the strip, stripe to GND |

At the firmware's brightness (64/255), 14 LEDs draw under 0.25 A.

### No 74AHCT125 yet? Bench workarounds

| Option | Wiring | When |
| --- | --- | --- |
| **Direct** (try first) | GPIO 23 → **330 Ω** → DIN; strip on 5V, common GND | 3.3 V is just under the strip's 3.5 V spec, but short strips usually read it. Fine if `RAINBOW` shows clean, steady colours |
| **Diode in the strip's 5 V** | 5V → **1N4001** (stripe toward the strip) → strip 5V; data as above | If the direct option flickers or shows wrong colours. The strip runs at about 4.3 V, so its threshold drops to about 3.0 V and 3.3 V data is a clean "1". Use a silicon diode (0.7 V drop), not a Schottky (only 0.3 V) |
| ✗ Capacitor in the data line | — | Blocks the steady levels the data needs: wrong colours |
| ✗ Resistor alone as a "shifter" | — | Can't raise a voltage. Keep the 330 Ω anyway: it protects the pin and damps ringing |
| ✗ BSS138 I2C shifter | — | Its 10 kΩ pull-ups make the edges too slow for 800 kHz |

Use the 74AHCT125 in the car: it works with any strip, at any temperature.

Test:
- `LEDSTRIP` steps through every pattern.
- `RAINBOW` flashes red → violet. If red shows as green, the colour order is wrong.

What the strip shows: [`../Docs/led-strip.md`](../Docs/led-strip.md).

---

## Voice: DFPlayer Mini (U10)

With the SD card slot toward you, pin 1 is top-left. Pins 1–8 run down the
left side and 9–16 down the right.

| DFPlayer pin | Name | Connect to |
| --- | --- | --- |
| 1 | **VCC** | **5V** |
| 2 | **RX** | **R11 1 kΩ** → **ESP32 GPIO 19** |
| 3 | **TX** | **ESP32 GPIO 20** |
| 4, 5 | DAC_R, DAC_L | not used |
| 6 | **SPK_1** | speaker wire 1 |
| 7 | **GND** | **GND** |
| 8 | **SPK_2** | speaker wire 2 |
| 9–16 | IO_1, GND, IO_2, ADKEY_1/2, USB+/−, BUSY | not used (pin 10 GND may also go to GND) |

- **Crossed TX/RX:** the ESP32's transmit pin (19) goes to the DFPlayer's
  receive pin (RX), and the DFPlayer's TX comes back to 20.
- **The 1 kΩ on RX** limits current between the 3.3 V pin and the 5 V module,
  and removes a hiss from the speaker.
- **Speaker LS2:** 4–8 Ω, up to 3 W, on SPK_1/SPK_2. The 3 W amplifier is
  built in, so neither wire goes to GND.
- **microSD:** FAT32, with an `mp3` folder at the top holding `0001.mp3`,
  `0002.mp3`, …. Copy `Voice/sd_card/mp3/` onto it.
- **Test:** `VOICE` should say `module answered`. `SAY:10` plays "This is a
  DRIFTS voice test."

Phrases and rules: [`../Docs/voice.md`](../Docs/voice.md).

---

## Vibration motors, piezo, status LEDs

| Part | Wiring |
| --- | --- |
| **4 motor modules** (GND, VCC, IN) | IN of all four ← R1 100 Ω ← GPIO 10, with R2 10 kΩ from IN to GND (holds them off during reset). VCC → 3V3 (or 5V if the modules are 5 V parts), GND → GND |
| **Piezo LS1** (passive) | GPIO 11 → R6 100 Ω → LS1 pin 1. LS1 pin 2 → R7 100 Ω → GPIO 18. Neither leg to GND, no flyback diode |
| **Green LED** | GPIO 2 → R5 100 Ω → anode (long leg); cathode → GND |
| **Yellow LED** | GPIO 3 → R8 330 Ω → anode; cathode → GND |
| **Red LED** | GPIO 4 → R9 330 Ω → anode; cathode → GND. Nothing else on GPIO 4 |

---

## 5 V rail budget (ESP32 side)

| Load | Typical | Peak |
| --- | --- | --- |
| ESP32-C6 (3 BLE links) | 80 mA | 300 mA |
| LCD + backlight | 25 mA | 30 mA |
| LED strip, 14 × WS2812B at 25 % | 40 mA | 250 mA |
| DFPlayer + 3 W speaker | 20 mA | 700 mA (loud) |
| Motor modules ×4 (from 3V3 or 5V) | 0 mA | 400 mA |
| **Total** | **~0.2 A** | **~1.7 A** |

- **On the bench:** USB power is fine for testing each part.
- **In the car, or with the voice loud and the alert running:** power the
  rail from the 5 V buck converter (U6, 3 A). Otherwise a brown-out resets
  the ESP32 mid-alert.

---

## Do not use

| GPIO | Why |
| --- | --- |
| 5, 15 | Strapping pins |
| 6, 7 | Pad JTAG |
| 8 | Strapping pin + on-board RGB LED |
| 9 | Strapping pin + BOOT button |
| 12, 13 | Native USB D− / D+ |
| 16, 17 | UART0 TX / RX to the CH343 USB chip (serial monitor) |
| 14, 24–30 | Not bonded out / SPI flash. These corrupt the flash silently; never use them, even though the WROOM-32 reference circuits do |
| 4 | Strapping pin: used for the red LED and **nothing else** |

**Free pins left:** 0, 1 (32 kHz crystal pads on some boards). Check before using them.

The authoritative list is [`reserved_pins.csv`](reserved_pins.csv).

More detail:
- [`schematic.md`](schematic.md): the circuits.
- [`wiring-check.md`](wiring-check.md): checks before power-on.
- [`../Docs/lcd-display.md`](../Docs/lcd-display.md): LCD bench test.
- [`../Tokyo/Docs/wiring.md`](../Tokyo/Docs/wiring.md): Tokyo (Pi 5) wiring.
