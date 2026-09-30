# DRIFTS — agent context

Read this before changing anything. It records what was built, what was
verified, what is known broken, and which decisions must not be silently undone.

**Project:** DRIFTS — driver fatigue detection. ELET 4308 senior design, UH.
**Repo:** KendracNguyen/DRIFTS_T1 · **Last updated:** 2026-09-30

---

## 1. Current state in one paragraph

An ESP32-C6 polls RPM, speed and throttle from a **BLE ELM327 dongle** and reads
longitudinal acceleration from an onboard MPU-6050. Rolling time-based windows
detect erratic driving; on a trigger the ESP32 sends `WAKE` to a Raspberry Pi
Zero 2 W over BLE. The Pi is supposed to run a MobileNetV2 eye-state classifier
and answer `DROWSY` / `NOT_DROWSY`, which the ESP32 turns into a motor-and-buzzer
alert. **The classifier does not exist** — see §4. The firmware has never been
compiled or run on hardware.

---

## 2. Branches

| Branch | What it is |
| --- | --- |
| `main` | v1. Pi-side scoring, simulated inputs, Flask API. Superseded |
| `NewBrain_David_V1` | David's v2 rewrite. Commit titled "NOT DEPLOYABLE AS IS" |
| `obd-ble-dongle` | Current work, based on v2. Not yet pushed or PR'd |

Base PRs on `NewBrain_David_V1`, not `main` — otherwise the diff includes the
whole v2 rewrite and reviewers cannot see what changed.

---

## 3. Landmines — read before editing

### GPIO 25 and 26 will corrupt flash, silently

The board is an **ESP32-C6-WROOM-1**, not an ESP32-WROOM-32. GPIO 24–30 are SPI
flash lines, not bonded out. The C6's GPIO validity mask still covers 0–30, so
`ledcAttach(26, …)` returns `true` and `digitalWrite(25, …)` runs without error.
The symptom is flash corruption or a boot loop with nothing pointing at the pins.

Motor is **GPIO 10**, buzzer is **GPIO 11**. `python3 hardware/check_pins.py`
enforces this; run it after touching `config.h`.

### "Listen-only OBD-II PIDs" is impossible — do not reintroduce it

A PID is a *reply*; an ECU sends one only in response to a request.
`TWAI_MODE_LISTEN_ONLY` transmits nothing. The original firmware combined both
and would have read 0.0 forever on a real car while appearing to run fine.

The BLE dongle resolves this because the dongle transmits. If anyone rewrites
this as "passive sniffing", they have reintroduced the bug. Passive sniffing of
manufacturer broadcast frames is a real but *different* design, needing
per-vehicle reverse engineering.

### Do not derive braking from the speed PID

PID `0x0D` has 1 km/h resolution and arrives at ~2 Hz, so one quantisation step
is ~0.06 g. Harsh braking comes from the MPU-6050. This is why the IMU is on the
ESP32 and not the Pi.

### `GATE_BENCH_MODE = true` is currently set

The 65 km/h operating-domain gate is disabled so the trigger can fire parked.
Production values are preserved as `MIN_SPEED_KPH_PRODUCTION` /
`GATE_COVERAGE_PRODUCTION`. **No data collected in bench mode is valid road
data.** Set it back to `false` before any driving trial.

### Three BLE connections is a hard ceiling

Dongle (central) + Pi + phone (peripheral) = 3. The prebuilt Arduino controller
for the C6 is compiled with `CONFIG_BT_LE_MAX_CONNECTIONS=3`. Raising
`MYNEWT_VAL_BLE_MAX_CONNECTIONS` achieves nothing — the host cannot exceed the
controller. There is no fourth link available.

### The C6 is single-core

BLE controller, BLE host and `loop()` share one 160 MHz core. Anything blocking
in `loop()` delays BLE housekeeping and can drop a connection. This is why all
scoring lives on the Pi and the ESP32 does I/O only. That split is load-bearing.

---

## 4. Known broken — the classifier is a constant function

**This blocks the project's core claim and is not fixed by any amount of OBD
work.**

`raspberry/model/` contains only `README.md` and `labels.txt`. There are no
trained weights. `detector.py` falls back to a heuristic that returns **`Open` at
0.85 confidence for every input**. Verified against dataset images, black frames
and random noise.

The filename hack in `_predict_fallback` does not even fire: `predict()` calls
`preprocess()` first, which converts a path to a PIL image, so the
`isinstance(…, Path)` check never matches.

**The Pi answers `NOT_DROWSY` to every WAKE regardless of what the camera sees.**

The 5 Pi tests pass anyway — they assert the result is well-formed, not correct.
Do not read them as evidence inference works.

Training is also not straightforward: the dataset is **168 images from 6 source
clips** of what appears to be one synthetic face, only one of which is a
non-drowsy clip. Frames within a clip are ~0.2 s apart and near-duplicates. A
fine-tune on this will memorise the face.

Two label problems to resolve before training:
- **PERCLOS appears inverted.** `Open` frames carry `perclos ≈ 0.25`, `Closed`
  frames `≈ 0.014`. If PERCLOS means proportion-closed, that is backwards.
  Confirm what Simuletic means before any of it reaches the report.
- **`Drowsy/Microsleep` is not a single-frame property.** Whether eye closure is
  a blink or a microsleep depends on duration, which a per-frame classifier
  cannot see. Recommended: train **two classes** (open/closed) and compute
  PERCLOS over a window on the Pi.

---

## 5. What is verified vs not

**Verified — host tests, no hardware** (`cd esp32/test && make`, 28 assertions):
rolling-window statistics (window length, stale-sample eviction, standard
deviation, gate in both modes, cooldown, rising-edge firing) and BLE stream
framing (CR-terminated writes, chunking, notification reassembly, ring-buffer
wrap, overflow accounting). Plus `hardware/check_pins.py`.

**Not verified — needs hardware:** everything NimBLE and ELMduino. Dongle
discovery, the GATT layout probe, ELM init, the poller, all three BLE links.
**The firmware has never been compiled** — the sandbox it was written in could
not reach Espressif's package host. Expect compile errors on first build.

---

## 6. Build requirements

- arduino-esp32 core **3.x**; board **ESP32C6 Dev Module** (`esp32:esp32:esp32c6`)
- Flash **8MB**, partition **`8M with spiffs`** or Huge APP — the dual-role BLE build does not fit the default
- **NimBLE-Arduino ≥ 2.3.9.** Earlier versions crash when scanning on the C6, which is the first thing this firmware does
- ELMduino (PowerBroker2), used unmodified via `ble_stream.h`
- Dongle must be **Bluetooth LE**. The C6 has no Classic Bluetooth radio at all

---

## 7. Layout

```
esp32/drifts_esp32/     firmware
  config.h              pins, thresholds, UUIDs, GATE_BENCH_MODE
  ble_stream.{h,cpp}    Stream over a NimBLE characteristic pair
  obd_ble.{h,cpp}       dongle discovery, GATT probe, ELM init, poller
  trigger.{h,cpp}       rolling windows, gate, cooldown
  imu.{h,cpp}           MPU-6050, harsh-brake events
  ble_hub.{h,cpp}       peripheral: Pi Link + Phone Link
esp32/test/             host tests, plain g++, no hardware
hardware/               pin spec, schematic, BOM, check_pins.py
docs/                   first-test walkthrough
raspberry/drifts/       Pi app (Python)
```

---

## 8. Immediate next tasks

1. **Compile the firmware.** Never done. Fix what surfaces
2. **Run `docs/first-test-walkthrough.md`** — dongle bring-up, four phases
3. **Rebuild the `SIM` command** dropped in the BLE rewrite, so the trigger chain is testable with no car. Scenario runner spec is in the task-definition doc
4. **Train a model or state plainly that inference is unimplemented.** Do not let the fallback stand in for it
5. **Rewrite the README** — it currently contains two architectures merged together and contradicts itself
6. **File IRB** before any drive that records a person. Four to eight weeks is normal

---

## 9. Conventions

- Commit messages explain *why*, not just what. Several fixes here are non-obvious and the reasoning is the valuable part
- Do not add a pin to `config.h` without adding it to `hardware/pinout.csv`; `check_pins.py` fails the build otherwise
- Host tests must pass before pushing: `cd esp32/test && make`
- Be explicit about what is measured vs assumed. The achieved OBD rate is
  vehicle-specific — the firmware reports it as `hz`; quote that, not a datasheet number
