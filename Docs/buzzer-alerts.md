# Buzzer Alerts: ESP32, Bluetooth Ping, and Phone/Laptop Simulation

Three ways the driver hears the fatigue alarm, all using the same pattern:
**a 1 s beep, a 1 s pause, repeating** while fatigue is detected.

| Setup | Who decides "fatigued" | Who beeps | Link |
| --- | --- | --- | --- |
| **Full system** | Pi camera + ESP32 alert score | ESP32 buzzer (GPIO 11/18) | Pi ↔ ESP32 over BLE |
| **Bluetooth ping** (test) | You | ESP32 buzzer | Any phone, laptop or the Pi, over BLE |
| **Simulation** (no Pi, no ESP32) | Laptop or phone camera + browser | The laptop or phone itself | None, or Wi-Fi to a second phone |

---

## 1. ESP32 buzzer pattern (firmware v15)

While the DROWSY alert runs the buzzer now sounds **1 s on, 1 s off**
(was 0.3 s / 0.3 s), for at most 20 s or until `STOP` / NOT_DROWSY.
`DROWSY_BUZZ_ON_MS` / `DROWSY_BUZZ_OFF_MS` in `config.h`. The motor keeps its
0.5 s / 0.3 s pulse.

---

## 2. Bluetooth ping: make the ESP32 buzzer beep from another device

Firmware v15 adds **`BEEP`**: one 1 s beep. It is accepted on the serial
console and on the ESP32's BLE "Pi result" characteristic, which any BLE
central can write to.

| From | How |
| --- | --- |
| **Pi** (or a laptop with Bluetooth) | `python -m drifts.beep` (`-n 3` for three beeps). On the Pi first `sudo systemctl stop drifts`: the ESP32 takes one Pi link at a time |
| **Laptop over USB** | `python -m drifts.beep --serial COM5`, or type `BEEP` in miniterm |
| **Any phone** | Free app **nRF Connect** or **LightBlue** → connect to `DRIFTS-ESP32` → characteristic `DF710003-B5A3-F393-E0A9-E50E24DCCA9E` → write text `BEEP` |
| **Live dashboard** | **Beep ESP32 buzzer** button (Home and Devices tabs) |

Notes:
- `BEEP` is ignored while an alert or pin test is running. The buzzer is
  already in use, and the log says so.
- A phone that writes to that characteristic is treated like the Pi. If it
  stays connected during a real alert without sending `PING`, the ESP32's
  3 s fail-safe ends the alert. That's fine for testing; disconnect the phone
  afterwards.

---

## 3. Simulation with only a laptop or phone

### What we need
The simulation runs fatigue detection with the device's own camera and
software, then sounds an alarm when fatigue is detected. There is no Pi and
no ESP32. Two cases:

- **A. One device:** the laptop runs detection and beeps itself.
- **B. Two devices:** the laptop runs detection; a phone on the dashboard
  beeps and vibrates. That's closer to the real product, where the alert is
  on the driver's side.

### Options researched

| Option | Works on | Pairing / setup | Verdict |
| --- | --- | --- | --- |
| **Browser Web Audio API** (oscillator tone in the dashboard page) | Every modern browser, laptop and phone | None. One tap to enable sound (autoplay rule) | ✅ **Chosen** |
| **Vibration API** (`navigator.vibrate`) | Android Chrome / Samsung Internet. **Not iOS**, not desktop Firefox | None | ✅ Added as an extra on Android |
| **Wi-Fi + HTTP** (phone opens the laptop's dashboard URL) | Any phone on the same network, or the laptop's hotspot | Same network | ✅ **Chosen** for case B: already how the dashboard works |
| OS sound from Python (`winsound.Beep` on Windows, terminal bell) | That laptop only | None | Possible for case A, but platform-specific; the browser covers it |
| **Web Bluetooth** (page talks BLE directly) | Chrome on Android / desktop. **Not Safari or any iOS browser** | Pick the device in a browser prompt | ❌ Excludes iPhones |
| **Laptop as a BLE peripheral** (Python `bless`) for a phone app to subscribe to | Laptop side yes; the phone needs a custom or generic BLE app | BLE pairing; `bless` is lightly maintained | ❌ Too much for a demo |
| Cloud push (Firebase etc.) | Any phone, any network | Accounts, internet | ❌ Overkill; adds latency and internet dependence |

**Decision:** Wi-Fi + browser. The dashboard page plays the alarm with the Web
Audio API (and vibrates on Android) when fatigue is detected. It runs on
whichever device opens the page: the laptop itself (case A), a phone on the
same Wi-Fi (case B), or both. No app install, no pairing, and it works on
iPhones.

### How to run it

```powershell
cd Raspberry
python -m drifts.live --esp sim --mirror          # laptop webcam, simulated car
```

- **Case A:** open `http://localhost:8080`. On Home, under *Active
  interventions*, switch on **Alarm on this device** (this tap unlocks
  browser audio).
- **Case B:** connect the phone to the same Wi-Fi as the laptop, or to the
  laptop's hotspot. Find the laptop's address (`ipconfig` → IPv4) and open
  `http://<laptop-ip>:8080` on the phone. Switch on **Alarm on this
  device** there. Windows may ask to allow Python through the firewall:
  allow it on private networks.
- **Test sound here** plays one beep to check the volume.

When the score reaches **Intervention 1 or 2** (eyes closed for a few
seconds), every device with the alarm switched on beeps **1 s on / 1 s off**
until the score recovers. In `--esp sim` mode a LOW score also marks the alert
as active, mirroring what the ESP32 does when the Pi confirms DROWSY.

### Limits to know
- **The sound must be switched on by a tap** on each device after the page
  loads. Browsers block sound until the user interacts with the page.
- **iPhone:** the ring/silent switch can mute web audio, and iOS has no web
  vibration. Turn the ringer on and the volume up.
- **Keep the screen on.** Phones throttle or stop pages in the background.
- Detection is the demo eye tracker (`eyes.py`), not the trained model
  (see `live-dashboard.md`).

### Sources
- [Chrome: Web Audio autoplay policy](https://developer.chrome.com/blog/web-audio-autoplay?hl=en)
  and [Chromium autoplay policy](https://www.chromium.org/audio-video/autoplay): the AudioContext
  stays suspended until `resume()` after a user gesture.
- [WebKit bug 149367](https://bugs.webkit.org/show_bug.cgi?id=149367): iOS needs a user gesture to start audio.
  [Matt Montag: unlocking Web Audio in Safari](https://www.mattmontag.com/web/unlock-web-audio-in-safari-for-ios-and-macos).
- [caniuse: navigator.vibrate](https://caniuse.com/mdn-api_navigator_vibrate) and
  [W3C Vibration implementation report](https://w3c.github.io/vibration/reports/implementation.html):
  Android browsers yes, WebKit/iOS no.
- [Web Platform features: Web Bluetooth](https://web-platform-dx.github.io/web-features-explorer/features/web-bluetooth)
  and [BeaconZone: browser support for Web Bluetooth](https://www.beaconzone.co.uk/blog/browser-support-for-web-bluetooth/):
  no Safari/iOS support.
- [bless on PyPI](https://pypi.org/project/bless/): Python BLE peripheral (GATT server) companion to bleak,
  which is central-only.
