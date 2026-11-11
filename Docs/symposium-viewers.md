# Symposium: Visitors View the Live Demo on Their Phones (planned)

**Status: agreed plan, not built yet.** We build this after the dev-view UI is
refined. Decisions so far (Nov 4 2026):

- Our own **commercial-grade hotspot** (not venue Wi-Fi).
- **Two printed QR codes:** join the Wi-Fi, then open DRIFTS.
- Visitors get a **view-only** dashboard with the **live camera**.

## Setup at the table

```
 Hotspot (commercial grade) ── Wi-Fi ──┬── Laptop: drifts.live --face mesh --viewers   ← does all the work
                                       │      └─ USB → ESP32 (LCD, strip, buzzer, motors, voice)
                                       └── Visitors' phones (view only)
```
- **Visitors connect to the laptop only, never to the Pi Zero.** The laptop
  runs the face mesh and serves everyone. In the car, the Zero is unaffected.
- The presenter's screen runs on the laptop at `http://localhost:8090/#dev`
  with full control.

### The two QR codes (printed, about 6 cm square)

| QR | Contents | Caption |
|---|---|---|
| 1 | `WIFI:T:WPA;S:<hotspot SSID>;P:<password>;;` | "1 · Scan to join the DRIFTS Wi-Fi" |
| 2 | `http://<laptop IP on the hotspot>:8090` | "2 · Scan to watch the live demo" |

- **Give the laptop a fixed IP** on the hotspot (a DHCP reservation in the
  hotspot's admin page), so the printed QR 2 never goes stale.
- **Print a typed fallback** under each code (SSID, password, URL) for phones
  that don't read Wi-Fi QR codes.
- A `/qr` page will also show both codes on screen. They're filled in from
  the hotspot name and the laptop's IP, which helps check the print.

## Build list

### 1. View-only mode for visitors (`--viewers`)
- **Who can control it:** only requests from the laptop itself (127.0.0.1)
  may control the demo. Every other device gets the page **read-only**.
- **Server-side enforcement:** blocked even if someone calls the address
  directly, not just hidden in the page. These are refused (403) for viewers:

  | Endpoint | Why it must be blocked |
  | --- | --- |
  | `POST /capture` | Saves camera frames to the laptop's disk |
  | `POST /calibrate` | Would reset the road pose mid-demo |
  | `POST /beep` | Sounds the ESP32 buzzer |
  | `POST /eye_send` | Would stop the eye score reaching the ESP32 |
  | `POST /glasses` | Changes the scoring weights |
  | `POST /dev_layers` | Changes the overlay on everyone's screen |
- **Page for viewers:** `status.json` tells the page it is a viewer. The page
  then hides the buttons (Calibrate, Capture, Beep, glasses Auto/On/Off,
  layer chips) and the Training tab, and shows a small "View only" badge.
- **Local alarm:** "Alarm on this device" and "Speak alerts" stay available
  to viewers, because they only affect the visitor's own phone.
- **Option:** an access token in QR 2 (`?k=…`) for presenter control from
  a second device, e.g. a tablet. Off by default.

### 2. Bandwidth and load
- **Viewer streams:** viewers get a separate, lighter stream, about 5 fps at
  JPEG quality 55, roughly 0.2–0.3 MB/s each. The presenter's stream stays
  full rate.
- **Viewer cap:** 20 by default, `--max-viewers` to change it. Beyond the
  cap, a viewer sees "Too many viewers: try again in a minute" instead of
  the video. Signals still update.
- **Encoding cost:** each viewer stream is encoded once and shared by all
  viewers, so the laptop's CPU cost doesn't grow with the number of phones.
- **Presenter's screen:** shows a viewer count, e.g. "14 watching".

### 3. Privacy (live camera agreed)
- Point the camera at the presenter's seat only. The frame must not include
  the aisle.
- **Table sign:** *"Live camera demo. Video is processed on this laptop and
  is not recorded or saved."*
- **Nothing is recorded:** capture is disabled for viewers (above), and the
  server keeps no video. That makes the sign accurate.
- **Fallback:** `--no-video-for-viewers` sends visitors only the face mesh
  and signals, for a presenter who doesn't want to be shown.

### 4. QR page and print sheet
- `GET /qr`: both QR codes, captions and the typed fallback, sized for A4 /
  Letter.
  - The Wi-Fi details come from `--hotspot-ssid` / `--hotspot-pass`.
  - The URL comes from the laptop's hotspot IP.
- **QR generation:** an offline Python library (`segno`, pure Python), so
  it works without internet at the venue.

## Before the symposium (checklist)
- [ ] Reserve the laptop's IP on the hotspot; print the QR sheet; test both
  codes on an iPhone and an Android phone.
- [ ] Load test: 15+ phones watching for 10 minutes. The presenter's screen
  must stay smooth, and the hotspot's admin page shows the load.
- [ ] Check from a phone that every control is refused (403) and the page shows "View only".
- [ ] Put the sign out; check the camera framing.
- [ ] Charge the hotspot or keep it on mains; bring a power strip.

## Not planned
- **No public internet tunnel** (ngrok, Cloudflare): anyone with the link,
  anywhere, could watch the camera.
- **No phone connections to the Pi Zero:** its Wi-Fi and CPU can't serve
  many phones, and it must stay free for the car.
