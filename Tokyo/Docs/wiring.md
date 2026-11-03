# Tokyo Wiring (Raspberry Pi 5): Rev 9

This follows the team's **DRIFTS Rev 9 wiring sheet**, with the changes
agreed on Nov 3 2026:

| Rev 9 sheet | Built as | Why |
| --- | --- | --- |
| M1-M4 on four pins (GPIO 5, 6, 13, 19), each with its own MOSFET | **4 motor modules, all IN pins on GPIO 13**, the same as the first model's rev 4 schematic | The modules have their own driver. GPIO 5/6 are pulled *up* at boot, so the motors could twitch at power-up |
| BZ1 passive piezo disc + 1 kΩ bleed | **3 V active magnetic buzzer from 3V3, 1N5819 across it** | That is the part we have. It is a coil: it needs a flyback diode, not a bleed resistor, and 5 V would overdrive it |
| Strip ≈13 LEDs | **14 LEDs** | 1 status pixel + 13-LED score bar, the same as the ESP32 |
| 12 V input: fuse only | **TVS diode (SMBJ24A or SMBJ26A) after F1; 5 A fuse; buck ≥ 5 A** | Car 12 V spikes to ~40 V at load dump. The Pi 5 alone can draw ~5 A |
| 74AHCT125 gates 2-4 unused | **2A, 3A, 4A tied to GND; 2OE-4OE to 5 V** | Floating CMOS inputs oscillate and put noise on the strip's data line |
| "Active-low outputs" | **Active-high** | An N-MOSFET on the low side turns on with a high gate |
| PWM labels "PWM0 ch0" on both 12 and 18 | Pi 5: GPIO 12 = ch0, 13 = ch1, 18 = ch2, 19 = ch3 | Those were the Pi 4's labels. On the Pi 5, all four are independent |

**No load connects straight to a Pi pin.** The Pi 5's GPIO pins are 3.3 V
and give only a few milliamps (up to ~12 mA).

## Pin map

| Pi 5 pin (physical) | GPIO | Goes to | Through |
| --- | --- | --- | --- |
| 33 | **GPIO 13** (PWM ch1) | IN of all 4 vibration motor modules | 100 Ω; 10 kΩ IN-to-GND |
| 12 | **GPIO 18** | Buzzer (Q5 gate) | 220 Ω gate resistor; 10 kΩ gate-to-GND |
| 19 | **GPIO 10** (SPI0 MOSI) | LED strip DIN | **74AHCT125** + 330 Ω |
| 32 | **GPIO 12** (PWM ch0) | IR LEDs (Q6 gate) | 220 Ω gate resistor; 10 kΩ gate-to-GND |
| 3 | **GPIO 2** (SDA) | LCD backpack SDA (and the MPU-6050, LV side) | BSS138 level shifter (A1→B1) |
| 5 | **GPIO 3** (SCL) | LCD backpack SCL | BSS138 level shifter (A2→B2) |
| 1 / 17 | 3V3 | shifter LV, buzzer +, MPU-6050 VCC | |
| 2 / 4 | 5V (from the buck converter) | motor module VCC, shifter HV, LCD VCC, 74AHCT125 VCC, strip, IR strings | |
| 6, 9, 14… | GND | everything, one common ground | |
| USB | — | USB audio adapter → PAM8403 → 3 W speaker | |
| CAM/DISP 0 | — | Camera Module 3 NoIR | 22-pin FPC "Standard–Mini" cable |

`install.sh` adds this to `/boot/firmware/config.txt` (reboot afterwards):
```
dtparam=i2c_arm=on
dtparam=spi=on
dtoverlay=pwm-2chan,pin=12,func=4,pin2=13,func2=4
```
Software check: `python -m tokyo test all` (add `--sim` to try it without hardware).

---

## Power (12 V → 5 V)

```
 12 V acc ── F1 5 A ──┬── D1 SMBJ24A (TVS, to GND) ── U1 buck IN (rated ≥ 24 V in, ≥ 5 A out)
                      │                                U1 OUT 5.1 V ── +5V rail ── Pi pins 2/4
                     GND ───────────────────────────── U1 GND ──────── Pi pin 6
 C1 470 µF across the 5 V rail near the motor modules
```
- **Powering the Pi through pins 2/4** skips its USB-C power negotiation. Tell
  the Pi the supply is good for 5 A, or it limits USB ports and warns about
  low voltage: `sudo rpi-eeprom-config -e` → add `PSU_MAX_CURRENT=5000`.
- **Never plug in the USB-C supply at the same time** as the 5 V rail.
- Add reverse-polarity protection on the 12 V input if the buck module lacks it.

## Vibration motors (GPIO 13): 4 modules, one pin

The same as the first model (rev 4 schematic): each module has **GND, VCC,
IN** and its own driver, and all four IN pins share one PWM pin.
```
 GPIO 13 ──[100 Ω]──┬── IN (M1) ── IN (M2) ── IN (M3) ── IN (M4)
                    │
                  10 kΩ (keeps the motors off while the Pi boots)
                    │
 GND ───────────────┴── GND of all 4 modules
 5V ─────────────────── VCC of all 4 modules (check the module rating; 3V3 can't supply 4 × 100 mA)
```
`[motors] duty = 1.0` (full on, 20 kHz), with a pulse pattern of 0.5 s on / 0.3 s off at Level 2.
GPIO 13 is pulled *down* at boot, so the motors stay off.

## Buzzer (GPIO 18): 3 V active magnetic buzzer

```
 3V3 (pin 1 or 17) ──┬──────────── BZ1 (+)  (longer leg / red wire)
                     │             BZ1 (−) ──┐
          D5 1N5819 (stripe to 3V3)          │ drain
                     └───────────────────────┤
 GPIO 18 ──[220 Ω]── gate   Q5 AO3400 (or IRLZ44N)
                     │                       │ source
                   10 kΩ                    GND
                     │
 GND ────────────────┘
```
- It beeps by itself when powered, so the Pi only switches it on and off
  (`[buzzer] kind = "active"`): 1 s on, 1 s off at Level 1 and 2.
- **3.3 V, not 5 V:** it is a 3 V part. If it must run from 5 V, put ~68 Ω in series.
- **Polarity matters.** Quick test: with Q5 off, touch BZ1 (−) to GND for a moment.

## LED strip (GPIO 10): WS2812B, 14 LEDs

```
                 74AHCT125 (U5, powered from 5 V)
 GPIO 10 (pin 19) ── 1A (pin 2)     1Y (pin 3) ──[330 Ω]── DIN  (arrow on the strip points away)
 GND ─────────────── 1OE (pin 1)    GND (pin 7) ── GND
 5V ──────────────── VCC (pin 14)
 GND ─────────────── 2A, 3A, 4A (pins 5, 9, 12): unused inputs never float
 5V ──────────────── 2OE, 3OE, 4OE (pins 4, 10, 13): unused outputs off

 +5V rail ──┬── strip 5V
       C2 1000 µF (at the strip end)
 GND ───────┴── strip GND
```
- The software drives it over **SPI** (pi5neo, `/dev/spidev0.0`). Enabling SPI
  also claims GPIO 7, 8, 9 and 11; nothing else uses them.
- 14 LEDs at 25 % brightness draw under 0.25 A; the software caps the strip at 1.5 A anyway.
- What it shows: [`../../Docs/led-strip.md`](../../Docs/led-strip.md). It is the same as the first model's ESP32 strip.

## IR illuminator (GPIO 12)

Two strings of two 940 nm IR LEDs, each string with its own resistor, both
switched by Q6. This is unchanged from Rev 9:
```
 +5V ──[R6c 22 Ω ½ W]── IR1 ── IR2 ──┐
 +5V ──[R6d 22 Ω ½ W]── IR3 ── IR4 ──┴── Q6 drain;  Q6 source ── GND
 GPIO 12 ──[220 Ω]── Q6 gate, 10 kΩ gate-to-GND
```
- `[ir] mode = "auto"`: on when the camera sees night, off in daylight.
- Steady on (`duty = 1.0`). If you dim it, the PWM runs at 25 kHz: a slow PWM
  shows as dark bands across the rolling-shutter camera image.
- The NoIR sensor is fairly insensitive at 940 nm. Check the night image;
  850 nm LEDs give a brighter picture but glow faintly red.

## Voice: USB audio adapter + PAM8403 + speaker

```
 Pi USB port ── USB audio adapter ── 3.5 mm out ── PAM8403 L in / GND
 PAM8403 5V / GND ── +5V rail / GND           PAM8403 L+ / L− ── 3 W 4-8 Ω speaker
```
- No GPIO pins are used, so this doesn't clash with GPIO 18 and 19.
- Find the adapter with `aplay -l` and set `[voice] device = "plughw:CARD=Device"`.
- Test: `python -m tokyo test voice`, or `python -m tokyo say 4`.
- Phrases: [`../../Docs/voice.md`](../../Docs/voice.md).

## LCD (GPIO 2 / 3)
The same as the first model ([`../../Hardware/pinout.md`](../../Hardware/pinout.md)):
through the BSS138 level shifter. **Never connect a 5 V-powered backpack's
SDA/SCL directly to the Pi.** The Pi 5 already has 1.8 kΩ pull-ups to 3.3 V on
GPIO 2/3. The MPU-6050 (GY-521) shares the 3.3 V side; Tokyo doesn't read it
yet (the driving data comes from the first model's ESP32).

## Camera Module 3 NoIR (arrives 11/8)
- 22-pin FPC to CAM/DISP 0. Lock the focus (`AfMode` manual) once the mount is fixed.
- FLT1, a 940 nm bandpass filter, sits in front of the lens (optical only).
- Test: `rpicam-hello -t 5000`.

## Power summary
| Load | Current (about) |
| --- | --- |
| Pi 5 (busy, with camera) | up to 5 A peak (typically 2-3 A) |
| 4 motor modules | 0.4-0.6 A |
| IR LEDs (2 strings × 100 mA) | 0.2 A |
| LED strip, 14 LEDs at 25 % | < 0.25 A |
| Speaker amplifier (3 W, loud) | up to 0.6 A |
| Buzzer, LCD, 74AHCT125, shifter | < 0.1 A |

All from the one 5 V buck converter, so it must be rated for **at least 5 A** (6-8 A gives headroom).
