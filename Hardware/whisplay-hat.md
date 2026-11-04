# PiSugar Whisplay HAT on the Pi Zero 2 W (first model)

**Status: ordered, not yet wired or coded (Nov 2026).** It replaces five parts
of the first model with one board that plugs onto the Pi Zero:

| Replaced | By |
| --- | --- |
| 16×2 I2C LCD + PCF8574 backpack | 1.69" colour LCD (240 × 280) |
| BSS138 level shifter (LCD) | not needed: everything is 3.3 V Pi logic |
| WS2812B strip (14 LEDs) | 1 RGB LED + a score bar drawn on the LCD |
| 74AHCT125 buffer (strip) | not needed |
| DFPlayer Mini + speaker | WM8960 codec + onboard or external speaker |

The ESP32 keeps the **buzzer, vibration motors and 3 status LEDs**, so the
alert still fires if the Pi is down (see [Architecture](#architecture)).

Sources: the PiSugar product page and wiki, and the pin definitions in
PiSugar's driver (`runtime/whisplay.py` in github.com/PiSugar/Whisplay).
Values PiSugar doesn't publish are marked *not published*.

---

## Datasheet

| Item | Value |
| --- | --- |
| Board | **65 × 30 mm**, the Pi Zero footprint; plugs onto the 40-pin header and stacks on top |
| Height / weight | *not published*. Measure it when it arrives, for the enclosure |
| Display | **1.69" IPS LCD, 240 × 280 px**, 16-bit colour, rounded corners (about 20 px) |
| Active area | about **30 × 37 mm** (retailer figure) |
| Display controller | **ST7789P3**, 4-wire SPI (mode 0); PiSugar's driver runs it at up to **100 MHz** on the Pi |
| Backlight | GPIO controlled: brightness by software PWM (newer boards) or on/off (older); **LOW = on** |
| Audio codec | **WM8960** (I2S audio + I2C control) on the listed board; PiSugar's unified driver also supports an **ES8389** variant. 48 kHz; ALSA card name `whisplaysound` either way |
| Speaker | **Onboard mono speaker**, plus a **PH2.0 2-pin socket** for an external speaker, with a switch between them. Power *not published*; the WM8960's class-D amplifier is typically up to about 1 W into 8 Ω |
| Microphones | 2 onboard (not used by DRIFTS yet) |
| RGB LED | 1, red/green/blue on separate GPIOs, brightness by software PWM; **LOW = lit** (the driver's "off" state is HIGH) |
| Button | **1 push button**, **HIGH = pressed**, no pull-up needed (pull-down on the board) |
| "Joy" variant | also has a **5-way joystick** (up/down/left/right/centre), active-low with internal pull-ups. Check which version you got |
| Power | from the Pi's 5 V / 3.3 V header pins; current *not published*. Budget about 0.1 A for the screen and up to about 0.5 A at loud audio |
| Works with | Pi Zero / Zero 2 W / Pi 5 (official page) |

---

## Pinout (40-pin header)

| Physical pin | GPIO (BCM) | Whisplay function | Direction |
| --- | --- | --- | --- |
| 19 | GPIO 10 | LCD SPI MOSI (SPI0) | out |
| 23 | GPIO 11 | LCD SPI SCLK (SPI0) | out |
| 24 | GPIO 8 | LCD SPI CE0 | out |
| 13 | GPIO 27 | LCD data / command (DC) | out |
| 7 | GPIO 4 | LCD reset (RST) | out |
| 15 | GPIO 22 | LCD backlight (LOW = on) | out |
| 22 | GPIO 25 | RGB LED red (LOW = lit) | out |
| 18 | GPIO 24 | RGB LED green (LOW = lit) | out |
| 16 | GPIO 23 | RGB LED blue (LOW = lit) | out |
| 11 | GPIO 17 | **Button** (HIGH = pressed) | in |
| 12 | GPIO 18 | I2S BCLK (audio) | out |
| 35 | GPIO 19 | I2S LRCLK (audio) | out |
| 38 | GPIO 20 | I2S data in (microphones) | in |
| 40 | GPIO 21 | I2S data out (speaker) | out |
| 3 | GPIO 2 | I2C1 SDA (WM8960 control) | bidir |
| 5 | GPIO 3 | I2C1 SCL (WM8960 control) | out |
| 2, 4 / 6, 9 … | 5V / GND | power | |
| 1, 17 | 3V3 | power | |
| *Joy variant:* 32, 33, 31, 29, 37 | GPIO 12, 13, 6, 5, 26 | joystick up, right, down, left, centre (active-low) | in |

**What's left free on the Pi Zero:** GPIO 14/15 (UART), GPIO 0/1 (HAT
EEPROM, reserved), GPIO 7, 9, 16. On the plain (non-Joy) version, GPIO 5, 6,
12, 13 and 26 are free too. The camera is on the separate CSI ribbon, so it
doesn't conflict.

**Tokyo (Pi 5) conflicts.** The Whisplay is *not* a drop-in for Tokyo's Rev 9
wiring:
- GPIO 10 is Tokyo's LED strip (SPI MOSI).
- GPIO 18 is Tokyo's buzzer.
- On the Joy variant, GPIO 12 and 13 are Tokyo's IR LEDs and motors.

Tokyo would need pin changes first.

---

## LCD: what DRIFTS will show (240 × 280, portrait)

The 16×2 screens map onto the colour display, with the score bar that the
14-LED strip used to show:

```
 ┌────────────────────┐   ┌────────────────────┐   ┌────────────────────┐
 │ DRIFTS        ●BLE │   │ DRIFTS        ●BLE │   │ ⚠ INTERVENTION 2  │  ← maroon / red
 │                    │   │                    │   │                    │
 │        87          │   │        62          │   │        38          │
 │   ALERT · /100     │   │  INTERVENTION 1    │   │  PULL OVER SAFELY  │
 │ ████████████████░░ │   │ ███████████░░░░░░░ │   │ ██████░░░░░░░░░░░░ │  ← score bar
 │   green 75-100 %   │   │  yellow            │   │  red, flashing     │
 │                    │   │ Signs of fatigue   │   │ Worsening signs    │
 │ Eyes 92  Drive 78  │   │ Eyes 55  Drive 80  │   │ Eyes 20  Drive 75  │
 │ OBD ● Pi ● 12:41   │   │ OBD ● Pi ● 12:44   │   │ Press to confirm ⏎ │
 └────────────────────┘   └────────────────────┘   └────────────────────┘
      OK (≥ 70)              Level 1 (50-69)            Level 2 (< 50)
```
- **Boot:** "Welcome to DRIFTS", then "Drive safe", as on the 16×2.
- **Score bar:** same bands as the strip ([`Docs/led-strip.md`](../Docs/led-strip.md)):
  green from 70, yellow 40–69, red 10–39, below 10 the whole bar flashes.
- **Status row:** OBD dongle and Pi links. A lost link shows a red dot and
  "connection lost".
- **Readability:** high contrast, digits at least 48 px tall. The backlight is
  dimmed at night (from the camera's night detection), so it doesn't dazzle
  the driver.
- The data comes from the ESP32's 1 Hz BLE telemetry: score, level, partial
  flag and driving score (firmware v16+).

## RGB LED

It plays the role of the strip's status pixel:

| Colour | Meaning |
| --- | --- |
| Green, solid | OBD dongle and ESP32 linked |
| Yellow, solid | Intervention 1 |
| Red, 1 s on / 1 s off | Intervention 2 / drowsy alert (in step with the buzzer) |
| Red, slow blink | Connection lost |
| Blue, breathing | Booting / screen locked |

## Button: proposed actions

| Press | Action | Why |
| --- | --- | --- |
| **Short press during an alert** | **"I'm awake":** ends the Level 3 check-in ("Are you okay? Press the button to confirm", clip 9) and is logged | Gives the driver a physical answer. It does **not** silence the buzzer or motors while the score stays low |
| **Short press, no alert** | Cycle the screen: score → details (eyes, driving, PERCLOS) → links | |
| **Long press (2 s)** | Recalibrate the road pose (look ahead, eyes open) | Same as the dashboard's Calibrate |
| **Double press** | Replay the last voice message | |

**Safety rule:** no button press may stop an alert while the driver is still
scored as drowsy. A press can only acknowledge the alert.

---

## Architecture

```
            BLE telemetry (score, level, links) ──►┐
 OBD ─BLE─► ESP32-C6 ──► buzzer, motors, 3 LEDs   │   (alert still works if the Pi is down)
                ▲                                  ▼
                └── EYE score ◄── Pi Zero 2 W + Whisplay HAT ──► colour LCD, RGB LED, speaker, button
                                    └── Arducam (CSI)
```
- **The Pi now drives the screen and voice.** If the Pi is booting or has
  crashed, they stop. The ESP32's buzzer, motors and LEDs still fire, so keep
  them wired.
- **ESP32 firmware v18 is unchanged.** Its 16×2 LCD, strip and DFPlayer code
  simply finds nothing connected.
- **Voice clips:** the same files, played by `aplay -D plughw:CARD=whisplaysound`
  (the WAVs in `Voice/clips/`), with the same rules as on the ESP32
  ([`Docs/voice.md`](../Docs/voice.md)). The button answers the check-in clip.
- **Pi Zero load:** the screen updates about once a second and audio is
  occasional, so it should add only tens of MB and a little CPU. Measure it,
  since it competes with the future goal of running the 478-point face mesh on
  the Zero.

## Install and first test (when it arrives)

1. Power off. Press the HAT firmly onto the Pi Zero's header (all 40 pins).
   Set the speaker switch to **onboard** for the first test.
2. Install PiSugar's driver on the Pi:
   ```bash
   git clone https://github.com/PiSugar/Whisplay.git --depth 1 ~/Whisplay
   cd ~/Whisplay && sudo bash install_driver.sh      # auto-detects the Pi (or script/install_raspberry_pi.sh)
   sudo reboot
   ```
3. Check each part:
   - **Audio card:** `aplay -l` lists **whisplaysound**.
   - **Speaker:** `aplay -D plughw:CARD=whisplaysound ~/drifts/Voice/clips/10_TEST.wav` plays "This is a DRIFTS voice test".
   - **LCD, LED and button:** run PiSugar's `example/test.py`.
4. Measure and note here: height with the HAT on, speaker loudness in the car,
   and the current draw (USB meter) when idle and while speaking.

## Open items
- [ ] Which version arrived: plain or **Joy**, V1 or V2, WM8960 or ES8389? PiSugar warns that V1's button circuit carries 5 V. That only matters on two non-Pi boards (Orange Pi Zero 3W, Radxa Cubie A7Z), not on the Raspberry Pi.
- [ ] Onboard speaker loud enough over road noise, or an external 8 Ω speaker on the PH2.0 socket?
- [ ] Enclosure cut-out for the 30 × 37 mm screen; mount it in the passenger's view, like the 16×2.
- [ ] Software: Pi-side BLE telemetry reader → LCD screens, RGB LED, voice, button (new module, `drifts/whisplay_ui.py`).
