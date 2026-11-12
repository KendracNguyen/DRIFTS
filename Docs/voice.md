# Spoken Alerts (Voice)

Every system speaks the same phrases, under the same clip numbers:

| System | Voice from | Plays through |
| --- | --- | --- |
| First model | **Pi Zero 2 W**, `drifts/whisplay_ui.py` | the **Whisplay HAT** speaker (onboard, or an external speaker on its PH2.0 socket) |
| Tokyo (Pi 5) | `tokyo/voice.py` | a small USB speaker |
| Simulation (laptop / phone) | the dashboard, using the browser's built-in speech | the device's speaker |

All of them play the WAV clips in [`Voice/clips/`](../Voice/clips) with
`aplay`. On the first model the voice follows the ESP32's alert level, which
the Pi reads over Bluetooth. The ESP32's buzzer and motors still fire on their
own if the Pi is down.

## Phrases

Edit [`Voice/phrases.json`](../Voice/phrases.json), then regenerate the clips.
Never renumber a phrase; add new ones at the end.

| # | Name | Said | When |
| --- | --- | --- | --- |
| 1 | READY | "DRIFTS ready. Drive safe." | a few seconds after start |
| 2 | CALIBRATED | "Calibration complete." | a calibration finished (Tokyo: road pose; first model: one started in the phone app) |
| 3 | LEVEL1 | "You seem tired. Consider taking a break soon." | entering Intervention 1 (score 50-69) |
| 4 | LEVEL2 | "Warning. Signs of drowsiness detected. Please pull over when safe." | entering Intervention 2 (< 50) or the drowsy alert; again every 60 s |
| 5 | RECOVERED | "Alertness back to normal. Thank you." | back to OK |
| 6 | OBD_LOST | "Vehicle data connection lost." | the OBD dongle stops for 5 s (it was there before) |
| 7 | ESP_LOST | "Alert unit disconnected." | first model: the Pi loses the ESP32 for 5 s |
| 8 | CAMERA_LOST | "Camera not detected." | Tokyo: no camera frames for 5 s |
| 9 | CHECK_IN | "Are you okay? Press the button to confirm." | reserved (Tokyo) |
| 10 | TEST | "This is a DRIFTS voice test." | tests |
| 11 | AWAKE | "Are you awake? Please say: I am awake." | first model: the Level 3 check-in, when an alert has lasted 20 s (the LCD shows "Awake Check: say 'I am awake'") |

Rules, the same on every system:
- **One clip at a time.** A clip waiting to play is replaced only by a more
  urgent one (AWAKE > LEVEL2 > LEVEL1 > the rest).
- **Repeats:** the Level 2 warning repeats every 60 s. On the first model a
  **button press acknowledges it**, which stops the voice repeats for 60 s
  but never silences the ESP32's buzzer or motors.
- **Links that were never connected stay silent,** so a bench test doesn't
  keep saying "connection lost".

## Making the clips

The voice is Piper (offline neural text-to-speech) with `en_US-libritts_r-medium`,
trained on LibriTTS-R (CC BY 4.0). Run this on a laptop; the Pis don't need Piper:
```bash
python -m venv piper-env && piper-env/bin/pip install piper-tts
piper-env/bin/python -m piper.download_voices en_US-libritts_r-medium
piper-env/bin/python Voice/make_clips.py --voice-dir .
```
This writes `Voice/clips/NN_NAME.wav`, which both Pis play as is.

## First model: the Whisplay HAT

- **Hardware and install:** [`../Hardware/whisplay-hat.md`](../Hardware/whisplay-hat.md).
  The sound card is `whisplaysound`.
- **Quick test:**
  ```bash
  aplay -D plughw:CARD=whisplaysound ~/drifts/Voice/clips/10_TEST.wav
  ```
- **Full test:** `python -m drifts.whisplay_ui --demo` runs OK → Level 1 →
  Level 2 → OK in 36 s, with screen, LED and voice, without the ESP32.
- **Settings:** `[whisplay]` in `Raspberry/config.toml` (`audio_device`,
  `voice`, `repeat_s`).

## Tokyo
`python -m tokyo test voice` plays every phrase, and `python -m tokyo say 4`
plays one. Wiring: [`Tokyo/Docs/wiring.md`](../Tokyo/Docs/wiring.md#voice-small-usb-speaker).

## Simulation
The dashboard's **Alarm on this device** card has a **Speak alerts** switch.
With it on, the browser says LEVEL1, LEVEL2 and RECOVERED at the same moments,
and holds the beep while it speaks. Browsers only allow sound after a tap, so
switch the alarm on by hand.
