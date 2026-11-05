# Whisplay HAT: Bring-up and Implementation Plan

The software for the first model's driver display is finished and tested
against a simulated HAT. This page covers three things:
- **Day 1:** what to do when the HAT arrives, step by step.
- **The side button:** how to program it.
- **The plan:** what comes after the screen works.

The hardware datasheet is in [`../Hardware/whisplay-hat.md`](../Hardware/whisplay-hat.md).

## Status

| Part | Code | Tested |
| --- | --- | --- |
| Screen: 16 views (score ring, driving, metrics, devices, setup, AI, rest stop, emergency) | `drifts/whisplay_ui.py` | simulated; PNGs in `Hardware/drawings/whisplay_screens.png` |
| RGB LED: green / yellow / red 1 s / red slow blink / blue | `led_color()` | simulated |
| Voice: clips on level changes and lost links | `VoiceDirector`, `Speaker` | simulated; clips play on a laptop |
| **Side button:** short / long / double press, each mapped in `config.toml` | `ButtonDecoder`, `WhisplayUI.do()` | simulated (26 Whisplay unit tests in all) |
| Joy version: 5-way joystick | `joystick = true` | simulated |
| Screen rotation (mount upside down) | `rotate = 180` | simulated |
| Clean shutdown: screen and LED off | `WhisplayUI.close()` | simulated |
| Bring-up self-test | `python -m drifts.whisplay_check --hw` | simulated |
| Installer: PiSugar driver, gpiod, spidev, groups, font | `WHISPLAY=1 bash deploy/install.sh` | not run on a Pi yet |

**Checked against PiSugar's own driver** (`runtime/whisplay.py` in
github.com/PiSugar/Whisplay, read Nov 2026):
- **Pins:** DC 27, RST 4, backlight 22, RGB 25 / 24 / 23, button 17. They match our pinout.
- **Button:** read every 5 ms. HIGH means pressed, and the callbacks take no arguments.
- **RGB LED:** `set_rgb(r, g, b)` takes 0–255 and inverts for the active-low LED itself.
- **Backlight:** PWM dimming on the Pi Zero 2 W (on/off only on the original Zero).
- **Screen:** 240 × 280 with 20 px rounded corners; `draw_image` takes our big-endian RGB565 bytes.
- **Not installed by default:** PiSugar's optional **`whisplay-daemon`** (app launcher) and
  **`whisplay-ai-chatbot`** (`chatbot.service`). Either one takes exclusive control of the screen, LED
  and button. DRIFTS refuses to start while one is running, and the installer disables both.

### Screens to check against

- **LCD**, every view with example data:
  ![LCD screens](../Hardware/drawings/whisplay_screens.png)
- **Bring-up check** screens (`whisplay_check --hw`):
  ![Self-test screens](../Hardware/drawings/whisplay_check_screens.png)
- **Phone app** (`drifts.live`, iPhone 13 width), the same design and alertness colours:
  ![Phone app](../Hardware/drawings/phone_screens.png)

Regenerate: `python -m drifts.whisplay_ui --gallery --out <folder>` and copy its
`whisplay_screens.png` to `Hardware/drawings` (LCD), and
`python -m drifts.whisplay_check --hw --sim` (self-test). The phone screenshots are taken with
Playwright from `drifts.live --esp sim --video Simuletic_DMS_Dataset/images`.

---

## Day 1: bring-up (about 30 minutes)

**Before it arrives:** the Pi Zero 2 W already runs 64-bit Raspberry Pi OS
with DRIFTS installed (`bash deploy/install.sh`), and SSH works.

1. **Fit the HAT.**
   - Power the Pi off.
   - Press the HAT onto all 40 header pins, straight and fully down.
   - Set the speaker switch to **onboard**.
   - Note which version it is: plain or **Joy** (has a joystick), and V1 or V2. Write it in the open items of `Hardware/whisplay-hat.md`.
2. **Install the driver:**
   ```bash
   cd ~/drifts/Raspberry && WHISPLAY=1 bash deploy/install.sh
   sudo reboot
   ```
   This clones PiSugar's driver to `~/Whisplay` and runs its installer
   (sound card overlay, SPI). It also installs `python3-libgpiod` and
   `python3-spidev` and adds you to `gpio`, `spi` and `audio`.
3. **Software check (no screen needed):**
   ```bash
   cd ~/drifts/Raspberry && .venv/bin/python -m drifts.whisplay_check
   ```
   Every line should be PASS. `[whisplay] enabled` shows WARN until step 6.
   Each FAIL line prints its fix.
4. **Hardware check:**
   ```bash
   .venv/bin/python -m drifts.whisplay_check --hw
   ```
   Answer y/n in the terminal at each step:

   | Step | What you should see | If not |
   | --- | --- | --- |
   | Screen | colour bars, grey steps, a red dot in each corner, TOP at the top | blank: HAT not seated. Upside down: `rotate = 180` |
   | Backlight | dims to 10 % and back | on/off only: older board, fine |
   | RGB LED | red, green, blue, white | note what showed instead |
   | Speaker | "This is a DRIFTS voice test" | speaker switch; `amixer -c whisplaysound sset speaker 90%` |
   | Microphone | say something: it records 3 s, prints the level and plays it back | `amixer -c whisplaysound sset mic 80%` |
   | Button | the screen ticks off **short**, **hold 1.5 s**, **double** | nothing: another program holds the HAT (the check says which) |

   The results are saved to `whisplay_check_report.txt`. Commit it, or paste it in the PR.
5. **Try the screens and the button** with example data (DEMO tag):
   ```bash
   .venv/bin/python -m drifts.whisplay_ui --browse     # every press is printed; Ctrl-C to stop
   .venv/bin/python -m drifts.whisplay_ui --demo       # OK -> Level 1 -> Level 2 -> OK, with voice, 36 s
   ```
6. **With the ESP32 (firmware v20):**
   - Set `enabled = true` under `[whisplay]` in `config.toml`.
   - Run `sudo systemctl restart drifts`.
   - The score appears a few seconds after the Bluetooth link comes up.
   - Unplug the OBD dongle: the LED blinks red and, after 5 s, the voice says the OBD link was lost.
7. **With the camera** (eyes, blinks, head pose and calibration on the screen):
   ```bash
   sudo systemctl stop drifts
   .venv/bin/python -m drifts.live --face mesh --esp ble --whisplay
   ```
   - A double press re-takes the road pose: the calibration screen shows.
   - Note the frame rate on the Device and dashboard pages. This is the first measurement for the Pi Zero face-mesh goal.

---

## The side button

**Detection:**
- **Short press:** pressed and released quickly. With a double press
  mapped, a short press waits 0.4 s in case a second press follows.
- **Long press:** held 1.5 s. It fires while still held.
- **Double press:** two presses within 0.4 s.

Set the actions in `Raspberry/config.toml`, then run `sudo systemctl restart drifts`:

```toml
[whisplay]
button_short = "next_page"       # default
button_long = "repeat_voice"     # default
button_double = "calibrate"      # default; "none" = no double press and no 0.4 s wait
long_s = 1.5
double_s = 0.4
```

| Action | Does |
| --- | --- |
| `next_page` / `prev_page` | score → driving → metrics → devices → setup → Driver AI |
| `page:<name>` | straight to `score`, `driving`, `vision`, `devices`, `setup` or `ai` |
| `repeat_voice` | plays the last voice message again |
| `calibrate` | re-takes the road pose (needs `drifts.live`; the car program shows "needs the camera") |
| `ai` | opens the Driver AI screen ("Assistant offline" until phase 4) |
| `brightness` | 30 → 70 → 100 % |
| `none` | nothing |

**Fixed safety rule:** during an alert (Intervention 1 or 2), a short or
double press **acknowledges** it, whatever the mapping says:
- The screen shows "Acknowledged".
- The voice repeat pauses for 60 s.
- The ESP32's buzzer and motors keep running.

The press is logged. A long press during an alert still repeats the message.

**From Python**, other programs can add their own actions with `hooks`:
`WhisplayUI(cfg, hooks={"calibrate": fn, "ai": fn})`. `drifts.live` uses
this for `calibrate`.

**Joy version** (`joystick = true`): up/left and down/right page through
the screens, and the centre press counts as a short press.

---

## Implementation plan

| Phase | What | Needs | Done when |
| --- | --- | --- | --- |
| **0: software** ✅ | Screens, LED, voice, programmable button, self-test, installer, docs | — | this PR |
| **1: bring-up** | Day 1 above. Record the version, orientation, LED order, speaker loudness and mic level | the HAT | `whisplay_check --hw` all PASS, report committed |
| **2: in the car** | `enabled = true`, ESP32 v20 over BLE. Measure the height with the HAT, loudness over road noise, and the current when idle and speaking (USB meter). Set the brightness for day/night | phase 1, the car | a drive with every level shown and heard; numbers in `whisplay-hat.md` |
| **3: camera on the Zero** | `drifts.live --face mesh --whisplay` on the Pi Zero. Measure fps and CPU with the screen running; the screen only redraws on change, ~10 ms a frame on a laptop | phase 1, Arducam | fps noted; decide whether the Zero runs the mesh or keeps the Haar tracker |
| **4: Driver AI (push to talk)** | Long press (or `button_long = "ai"`) records from the HAT's mics: listening → thinking → answer screens, which are already drawn. **Fixed intents first**, answered from DRIFTS data with Piper speech, no internet needed: "what's my score", "why did it drop" (the measured signals that changed), "is my camera connected". Then optional speech-to-text and a language model | phase 1; a speech-to-text choice (below) | 4 intents work parked, answers use only measured values |
| **5: phone app** | ✅ 4 tabs (Home, History, Devices, Settings) in the mockup's design, band colours from `ui_model.py`, real trip history. Next: the AI tab | — | AI tab with phase 4 |
| **6: rest stop and emergency** | Real data only: GPS or the phone's location for rest stops, contacts from the phone app, confirmed calls. Opt-in "notify a contact after sustained high fatigue" with a cancel period | phase 5 | screens leave "unavailable" with real data |

**Phase 4 options.** PiSugar's `whisplay-ai-chatbot` is a reference, not
something to copy:
- It is **GPL-3.0**: copying its code would make DRIFTS GPL too.
- It is mostly **Node.js**.
- It **takes over the HAT** as `chatbot.service`.

What we can reuse is its approach: press, speak, answer, on the same HAT.

Speech-to-text options:

| Option | On the Pi Zero 2 W | Notes |
| --- | --- | --- |
| Fixed phrases (Vosk small model, ~40 MB) | runs, a few phrases | offline; enough for the 4 intents |
| Whisper tiny (whisper.cpp) | slow: seconds per phrase | offline; better on Tokyo's Pi 5 |
| Cloud speech-to-text + language model | fast | needs internet and an API key in the car; must never be needed for an alert |

Whatever we choose, **the alert never depends on the assistant**. The ESP32
fires the buzzer and motors on its own, and the Pi's voice warnings are
pre-recorded clips.

## Risks and open questions

- **Another program holding the HAT.** `whisplay-daemon` or `chatbot.service` (for example after trying PiSugar's demos) blocks DRIFTS. The check and the installer both catch this.
- **Joy version.** Its joystick uses GPIO 5, 6, 12, 13 and 26. Nothing else on the Pi Zero uses them. It would clash with Tokyo's pins, but Tokyo doesn't use the Whisplay.
- **V1 boards.** PiSugar warns the V1 button circuit carries 5 V. This only matters on two non-Pi boards, not on the Raspberry Pi.
- **CPU on the Pi Zero.** The screen, the three LED PWM threads, BLE and the camera share 4 small cores. Measure in phase 3.
- **TypixNode.** PiSugar's TypixNode docs page couldn't be opened from the build environment, so it isn't covered here. If it is the hardware we bought, check its pins against the table above before step 1.
