# Pinout (first model)

Every wire, as of **ESP32 firmware v19** and **schematic rev 6**:

| Board | Drives |
| --- | --- |
| **ESP32-C6** | OBD (BLE), MPU-6050, piezo buzzer, vibration motors, 3 status LEDs: the alert, which works on its own |
| **Pi Zero 2 W + Whisplay HAT** | camera, driver screen, RGB LED, voice, button |

![DRIFTS ESP32-C6 pinout](pinout.svg)

Whole-system drawing: [`drawings/DRIFTS_Schematic_rev6.pdf`](drawings/DRIFTS_Schematic_rev6.pdf)
(PNG alongside). Both drawings are generated:
`python Hardware/drawings/draw_schematic.py` and `python Hardware/drawings/draw_pinout.py`.

---

## ESP32-C6

Find pins by the **label printed on the board** (`5V`, `3V3`, `G`/`GND`, `2`,
`3`, `4`, `10`, `11`, `18`, `21`, `22`), not by position. Clone boards with the
CH343 USB chip place pins differently from the Espressif DevKitC.

| ESP32-C6 pin | Goes to | Through | Firmware constant |
| --- | --- | --- | --- |
| **5V** | Motor module VCC if they are 5 V parts | — | — |
| **3V3** | MPU-6050 VCC, motor module VCC (3.3 V modules) | — | — |
| **GND** | Everything: one common ground | — | — |
| **GPIO 2** | Green LED anode → cathode to GND | R5 100 Ω (330 Ω if yellow-green) | `LED_GREEN_PIN` |
| **GPIO 3** | Yellow LED anode → cathode to GND | R8 330 Ω | `LED_YELLOW_PIN` |
| **GPIO 4** | Red LED anode → cathode to GND | R9 330 Ω | `LED_RED_PIN`. LED only: strapping pin |
| **GPIO 10** | **IN** of all 4 vibration motor modules | R1 100 Ω; R2 10 kΩ IN-to-GND | `MOTOR_PIN` |
| **GPIO 11** | Piezo LS1 pin 1 | R6 100 Ω | `PIEZO_A_PIN` |
| **GPIO 18** | Piezo LS1 pin 2 (neither leg to GND) | R7 100 Ω | `PIEZO_B_PIN`: the same signal as 11, inverted |
| **GPIO 21** | MPU-6050 SDA | — | `IMU_SDA_PIN` |
| **GPIO 22** | MPU-6050 SCL | — | `IMU_SCL_PIN` |

`python3 Hardware/check_pins.py` checks this table (from `pinout.csv`) against
`config.h` and the reserved-pin list. It must print `OK: 8 pins agree`.

### Parts on the ESP32

| Part | Wiring |
| --- | --- |
| **MPU-6050 (GY-521)** | VCC → 3V3, GND → GND, SDA → GPIO 21, SCL → GPIO 22, AD0 → GND (address 0x68). It's a 3.3 V part: no level shifter. Test: `I2C SCAN` lists `0x68` |
| **4 motor modules** (GND, VCC, IN) | IN of all four ← R1 100 Ω ← GPIO 10, with R2 10 kΩ from IN to GND (holds them off during reset). VCC → 3V3 (or 5V if the modules are 5 V parts), GND → GND |
| **Piezo LS1** (passive) | GPIO 11 → R6 100 Ω → LS1 pin 1. LS1 pin 2 → R7 100 Ω → GPIO 18. Neither leg to GND, no flyback diode |
| **Green LED** | GPIO 2 → R5 100 Ω → anode (long leg); cathode → GND |
| **Yellow LED** | GPIO 3 → R8 330 Ω → anode; cathode → GND |
| **Red LED** | GPIO 4 → R9 330 Ω → anode; cathode → GND. Nothing else on GPIO 4 |

**Free ESP32 pins:** 19, 20, 23 (and 0, 1, which are the 32 kHz crystal pads
on some boards; check before using them).

### Do not use

| GPIO | Why |
| --- | --- |
| 5, 15 | Strapping pins |
| 6, 7 | Pad JTAG |
| 8 | Strapping pin + on-board RGB LED |
| 9 | Strapping pin + BOOT button |
| 12, 13 | Native USB D− / D+ |
| 16, 17 | UART0 TX / RX to the CH343 USB chip (serial monitor) |
| 14, 24–30 | Not bonded out / SPI flash. These corrupt the flash silently; never use them, even though ESP32-WROOM-32 reference circuits do |
| 4 | Strapping pin: used for the red LED and **nothing else** |

The authoritative list is [`reserved_pins.csv`](reserved_pins.csv).

---

## Pi Zero 2 W + Whisplay HAT

The **Whisplay HAT plugs onto all 40 header pins**: no wires. The camera uses
the separate CSI ribbon connector. Full datasheet, screens, LED and button
behaviour: [`whisplay-hat.md`](whisplay-hat.md).

| Physical pin | GPIO | Whisplay function |
| --- | --- | --- |
| 19 / 23 / 24 | 10 / 11 / 8 | LCD SPI0 MOSI / SCLK / CE0 |
| 13 / 7 / 15 | 27 / 4 / 22 | LCD data-command / reset / backlight (LOW = on) |
| 22 / 18 / 16 | 25 / 24 / 23 | RGB LED red / green / blue (LOW = lit) |
| 11 | 17 | Button (HIGH = pressed) |
| 12 / 35 / 38 / 40 | 18 / 19 / 20 / 21 | Audio I2S |
| 3 / 5 | 2 / 3 | Audio codec I2C1 |
| 2, 4 / 6 | 5V / GND | Power from the Pi |

**Power for the Pi Zero + HAT:**
- On the bench, the Pi's own USB supply is enough: 5 V 2.5 A.
- In the car, use the 12 V → 5 V buck converter (3 A) on the Pi's 5V/GND
  pins. The speaker's current bursts are on the Pi side.

---

More detail:
- [`schematic.md`](schematic.md): the circuits.
- [`wiring-check.md`](wiring-check.md): checks before power-on.
- [`whisplay-hat.md`](whisplay-hat.md): the driver screen.
- [`../Tokyo/Docs/wiring.md`](../Tokyo/Docs/wiring.md): Tokyo (Pi 5) wiring.
