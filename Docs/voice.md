# Spoken Alerts (Voice)

Both systems speak the same phrases, under the same clip numbers:

| System | Voice from | Plays through |
| --- | --- | --- |
| First model | **ESP32-C6** + DFPlayer Mini (MP3 module with a 3 W amplifier) | 3 W 4-8 Ω speaker on the DFPlayer |
| Tokyo (Pi 5) | `tokyo/voice.py`, `aplay` | MAX98357A I2S amplifier (GPIO 18/19/21) → 3 W speaker |
| Simulation (laptop / phone) | The dashboard, using the browser's built-in speech | The device's speaker |

On the first model the ESP32 speaks, not the Pi Zero. The ESP32 already
decides the alert levels, so the voice keeps working even if the Pi crashes.

## Phrases

Edit [`Voice/phrases.json`](../Voice/phrases.json), then regenerate the
clips. Never renumber a phrase; add new ones at the end.

| # | Name | Said | When |
| --- | --- | --- | --- |
| 1 | READY | "DRIFTS ready. Drive safe." | 3 s after power-up |
| 2 | CALIBRATED | "Calibration complete." | Tokyo: road-pose calibration done. First model: the Pi sends `SAY:2` |
| 3 | LEVEL1 | "You seem tired. Consider taking a break soon." | entering Intervention 1 (score 50-69) |
| 4 | LEVEL2 | "Warning. Signs of drowsiness detected. Please pull over when safe." | entering Intervention 2 (< 50) or the drowsy alert; again every 60 s |
| 5 | RECOVERED | "Alertness back to normal. Thank you." | back to OK |
| 6 | OBD_LOST | "Vehicle data connection lost." | driving data stops for 5 s (it was there before) |
| 7 | PI_LOST | "Camera unit disconnected." | first model: the Pi drops for 5 s |
| 8 | CAMERA_LOST | "Camera not detected." | Tokyo: no camera frames for 5 s |
| 9 | CHECK_IN | "Are you okay? Press the button to confirm." | reserved for the Level 3 check-in |
| 10 | TEST | "This is a DRIFTS voice test." | `SAY:10` |

Rules, the same on every system:
- **One clip at a time.** A clip waiting to play is replaced only by a more urgent one (LEVEL2 > LEVEL1 > the rest).
- **During an alert, the buzzer holds off while a clip plays**, then rejoins its 1 s on / 1 s off pattern. The motors keep going.
- Links that were never connected stay silent, so a bench test doesn't keep saying "connection lost".

## Making the clips

The voice is Piper (offline neural text-to-speech) with `en_US-libritts_r-medium`,
trained on LibriTTS-R (CC BY 4.0). Run this on a laptop; the Pis don't need Piper:
```bash
python -m venv piper-env && piper-env/bin/pip install piper-tts
piper-env/bin/python -m piper.download_voices en_US-libritts_r-medium
piper-env/bin/python Voice/make_clips.py --voice-dir .
```
This writes:
- `Voice/clips/NN_NAME.wav`, which Tokyo plays.
- `Voice/sd_card/mp3/NNNN.mp3` (needs ffmpeg), for the DFPlayer.
- `Esp32/drifts_esp32/voice_clips.h`, with the clip numbers and lengths. Rebuild the firmware after changing phrases.

## First model: DFPlayer Mini on the ESP32-C6

The team's reference circuit uses an **ESP32-WROOM-32** board. Use the **same
connections on the C6, but on these pins**:

| DFPlayer pin | Reference circuit (WROOM-32) | **DRIFTS (ESP32-C6)** |
| --- | --- | --- |
| VCC (pin 1) | VIN (5 V) | **5V** |
| RX (pin 2) | a GPIO (TX side) | **GPIO 19 through a 1 kΩ resistor** (`VOICE_TX_PIN`) |
| TX (pin 3) | a GPIO (RX side) | **GPIO 20** (`VOICE_RX_PIN`) |
| SPK1 (pin 6) | speaker | speaker |
| GND (pin 7 or 10) | GND | **GND** |
| SPK2 (pin 8) | speaker | speaker |

- **Don't copy the WROOM-32 GPIO numbers.** On the C6, GPIO 24-30 are the
  flash lines, and 25/26/27 in particular corrupt the flash
  ([`Hardware/pinout.md`](../Hardware/pinout.md)).
- **The 1 kΩ on DFPlayer RX** is the module maker's recommendation. It limits
  current between the 3.3 V pin and the 5 V module, and it removes a hiss
  from the speaker.
- **Speaker** goes on SPK1/SPK2 (a 3 W amplifier is built in), 4-8 Ω, up to 3 W.
  Don't connect either speaker wire to GND.
- **microSD card:** FAT32, with a folder named `mp3` at the top holding
  `0001.mp3`, `0002.mp3`, … Copy `Voice/sd_card/mp3/` onto the card as it is.

Firmware v17: `VOICE_ENABLED`, the pins and `VOICE_VOLUME` (0-30) are in `config.h`.

### Serial commands
| Command | Does |
| --- | --- |
| `SAY:4` | speak clip 4 (also accepted from the Pi over BLE) |
| `VOICE` | `[VOICE] enabled \| module answered \| last clip 4 \| idle` |

**Bench test:** flash v17. "DRIFTS ready" plays 3 s after boot. Then send:
- `SAY:10`: plays the voice test.
- `SCORE 60`: plays the Level 1 warning.
- `SCORE 30`: plays the Level 2 warning.
- `SCORE 90`: plays "back to normal". `SCORE OFF` then returns to the real score.

If `VOICE` says *no reply*, check:
- TX and RX are not swapped.
- The 1 kΩ resistor is on DFPlayer RX.
- The DFPlayer has 5 V.
- The SD card is FAT32 with an `/mp3` folder.

`[VOICE] DFPlayer error 6` means the card has no `/mp3/000N.mp3` for that clip.

## Tokyo
`python -m tokyo test voice` plays every phrase. `python -m tokyo say 4` plays
one phrase. Wiring is in [`Tokyo/Docs/wiring.md`](../Tokyo/Docs/wiring.md#voice-max98357a-i2s-amplifier--speaker).

## Simulation
The dashboard's **Alarm on this device** card has a **Speak alerts** switch.
With it on, the browser says LEVEL1, LEVEL2 and RECOVERED at the same moments,
and holds the beep while it speaks. Browsers only allow sound after a tap, so
switch the alarm on by hand.
