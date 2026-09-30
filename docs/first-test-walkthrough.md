# DRIFTS — First Test Walkthrough

**Audience:** software team · **Duration:** ~2 hours · **Needs:** laptop, ESP32-C6,
BLE dongle, a car you can sit in with the ignition on

---

## Read this before you start

**What this session proves:** the pipeline works end to end — the dongle
connects, PIDs arrive at a measurable rate, statistics accumulate, an anomaly
wakes the Pi, the Pi answers, the alert fires and stops.

**What this session cannot prove:** that the system detects drowsiness.

There are no trained model weights in the repo. `raspberry/model/` contains only
`README.md` and `labels.txt`. `detector.py` therefore runs in its `fallback`
backend, which returns **`Open` at 0.85 confidence for every input** — verified
against dataset images and synthetic frames alike. The Pi will answer
`NOT_DROWSY` to every WAKE regardless of what the camera sees.

So if someone sits in front of the camera with their eyes shut and the system
says `NOT_DROWSY`, **that is the expected result today.** It is not a bug to
chase. Write it in the log and move on.

**The gate is in bench mode.** `GATE_BENCH_MODE = true` in `config.h` disables
the 65 km/h operating-domain gate so the trigger can fire while parked. The
firmware prints a banner at boot, tags triggers `[BENCH]`, and reports
`"gate":"BENCH"` in telemetry. **No data collected in this mode is valid as road
data.** Set it back to `false` before any real driving trial.

---

## Phase 0 — Build environment (30 min, do this first)

Nothing below works until the toolchain is right, and three of these settings
produce confusing failures rather than clear errors.

- [ ] Arduino IDE with **arduino-esp32 core 3.x** (C6 support starts at 3.0.0)
- [ ] Board: **ESP32C6 Dev Module**
- [ ] **Flash Size: 8MB** (default is 4MB and silently wastes half the part)
- [ ] **Partition Scheme: `8M with spiffs`** or **Huge APP** — the dual-role BLE build does not fit the default
- [ ] **NimBLE-Arduino 2.3.9 or newer.** Older versions *crash when scanning on the C6*, which is the first thing this firmware does. Do not skip this.
- [ ] **ELMduino** (PowerBroker2) from Library Manager
- [ ] Leave `MYNEWT_VAL_BLE_MAX_CONNECTIONS` alone. The C6 controller caps at 3, which is exactly what we use.

**Pin check before you power anything:**

```bash
python3 hardware/check_pins.py
```

Must print `OK: 5 pins agree`. Confirm nothing is wired to **GPIO 25 or 26** —
on the C6 those are SPI flash lines, and using them corrupts flash with no error
message.

**Host tests, on your laptop, no hardware:**

```bash
cd esp32/test && make        # expect ALL PASS twice
cd raspberry && python3 -m pytest tests/ -q
```

Note the Pi tests pass against the broken detector — they check the result is
well-formed, not correct. Do not read them as evidence inference works.

---

## Phase 1 — ESP32 alone (20 min)

No dongle, no Pi. Confirm the board is alive before adding radios.

1. Flash. Open Serial Monitor at **115200**.
2. Confirm the boot banner, including the `GATE_BENCH_MODE = true` warning block.
3. Confirm `[IMU] MPU-6050 at 0x68`.
   - Absent → I2C wiring or address. Check `AD0` is tied low.
4. Type `STATUS`. Expect `obd=scanning`, `hz=0.0`, zero samples.
5. Type `BRAKE` three times, then `STATUS`. `brakes` should read 3.
6. Type `DROWSY`. **Motor and buzzer should run and keep running** up to
   `MAX_ALERT_MS` (20 s).
   - If they stop after ~3 s you are on old firmware — the fail-safe used to
     fire when no Pi had ever connected. Re-pull and reflash.
7. Type `STOP`. Both stop.

**Record:** does the IMU initialise, and does the alert hold for its full duration?

---

## Phase 2 — Dongle on a desk (30 min)

Dongle **not** in a car yet. This isolates the BLE layer from the vehicle.

> **Before you begin:** make sure nobody's phone is connected to the dongle.
> A BLE dongle serves **one** central at a time — Torque or Car Scanner open in
> the background makes the ESP32 silently fail to connect.

1. Power the dongle (any 12 V source, or a car with the ignition off).
2. Watch the serial log for: `Scanning` → `Candidate:` → `Connected, MTU` →
   `Matched layout: …`
3. **If no layout matches**, the log lists every service with its notify/write
   counts. Open **nRF Connect** on a phone, read the dongle's real UUIDs, and add
   them to `LAYOUTS[]` in `obd_ble.cpp`. This is a 5-minute fix, not a blocker.
4. Expect `ELM327 init failed` or a stall at this stage — **that is correct**.
   The dongle is reachable but no vehicle is answering.

**Record:** which GATT layout matched, and the dongle's MAC.

---

## Phase 3 — Dongle in the car (45 min) ⭐ the real test

Car parked, **ignition on** (engine running is better — idle RPM varies, which
the trigger can actually see).

1. Plug the dongle in. Power the ESP32 from a laptop or power bank.
2. Watch for `ELM327 ready` and the supported-PID line.
3. Wait 30 seconds. Type `STATUS`.

**This is the measurement that matters:**

```
[STATUS] obd=polling layout=... 8.4 Hz | rpm 780 (sd 42, n 58) spd 0 (sd 0.0, n 58) ...
```

| Field | Expect | If wrong |
| --- | --- | --- |
| `obd=` | `polling` | `scanning` → dongle unreachable. `backoff` → see the drop reason above |
| `hz` | **2–11** | `0.0` with `polling` → dongle is connected but the car is not answering. Check ignition, then `ELM_PROTOCOL` in `config.h` |
| `n` (samples) | rising | Stuck at 0 → PIDs unsupported or parse failing |
| `ovf` | **0** | Nonzero → frames were dropped; any statistic from them is suspect |
| `gate` | `BENCH` | `closed` → you are not running bench mode |

**Write the `hz` number down.** It is vehicle-specific, gateways throttle it,
and the report should quote what you measured, not what a datasheet claims.

4. **Verify the IMU sign.** With someone else driving slowly and safely, brake
   firmly. Expect `[IMU] Harsh brake: -0.4 g`.
   - If firm *acceleration* produces it instead, flip `IMU_LONG_SIGN` in
     `imu.h`. Getting this backwards makes every brake count meaningless and
     nothing else will tell you.

5. **Provoke a trigger.** Rev the engine up and down for ~30 s. RPM standard
   deviation should cross 400 and fire `[TRIGGER] [BENCH] RPM_FLUCTUATION`.

**Record:** achieved Hz, which PIDs are supported, whether a trigger fired, and
the IMU sign.

---

## Phase 4 — Full chain with the Pi (30 min)

1. Start the Pi app: `cd raspberry && python3 -m drifts --debug`
2. Confirm on the Pi: `Connected to ESP32 hub`, `Subscribed to WAKE`, and
   `Heartbeat started (1.0 s interval)`.
   - **No heartbeat line → alerts will cut out after 3 s.** Pull latest.
3. Confirm on the ESP32: `[BLE] Pi subscribed` and the status LED lights.
4. Trigger a WAKE (rev the engine, or type `WAKE`).
5. Expected sequence:
   - ESP32: `[BLE] WAKE -> Pi`
   - Pi: `WAKE event received! Starting camera...`
   - Pi: `Cycle complete. Result: NOT_DROWSY` ← **expected, see the top of this doc**
   - ESP32: `[MAIN] Pi result: NOT_DROWSY`
6. Force the alert path: type `DROWSY` on the ESP32 serial. Motor and buzzer run
   and **hold** (the heartbeat is keeping the fail-safe satisfied).
7. **Test the fail-safe.** With the alert running, kill the Pi process
   (Ctrl-C). The alert must stop within ~3 s, logging
   `fail-safe: Pi silent`.

Step 7 is the one worth demonstrating to Dr. Moses. It is a safety property,
not a feature.

---

## Phase 5 — Three links at once (15 min)

Connect the phone app while the dongle and Pi are both up. Three concurrent BLE
connections is the **hard ceiling** on the C6 — there is no fourth.

- [ ] All three connected simultaneously
- [ ] Held for 10 minutes with no drop
- [ ] `hz` does not collapse when the phone connects
- [ ] Disconnect the phone, reconnect it — it comes back

If the third connection is refused, a previous link probably did not tear down.
Power-cycle and retry before assuming a code fault.

---

## Log template

Copy this into the team notes and fill it in as you go.

```
Date:              Vehicle (year/make/model):
Dongle model:      GATT layout matched:
Achieved hz:       ovf count:
Supported PIDs:    RPM __  SPEED __  THROTTLE __  LOAD __
IMU sign correct:  Y / N   (flipped? Y / N)
Trigger fired:     Y / N   reason: ____________
Pi WAKE received:  Y / N
Alert held:        Y / N
Fail-safe worked:  Y / N
3 BLE links held:  Y / N   duration: ____
Surprises:
```

---

## Known issues — do not debug these

| Symptom | Why | Action |
| --- | --- | --- |
| Pi always says `NOT_DROWSY` | No trained model; detector is a constant | Expected. Log it |
| Pi tests pass despite that | Tests check shape, not correctness | Expected |
| Trigger fires while parked | `GATE_BENCH_MODE = true` | Expected today |
| `NO DATA` in the log | Ordinary reply to an unsupported PID | Counted, not a fault |
| `SEARCHING...` stall on first request | Protocol auto-detect, up to 20 s | Normal. Set `ELM_PROTOCOL` to `'6'` to skip it |
| No `SIM` command | Dropped in the BLE rewrite, not yet rebuilt | Use `WAKE` / `DROWSY` / `BRAKE` instead |

---

## Before the next session

- [ ] Set `GATE_BENCH_MODE = false` if any road data will be collected
- [ ] Tune thresholds against the `hz` and standard deviations actually measured
- [ ] Train a model, or state plainly in the report that inference is unimplemented
- [ ] File the IRB application if it is not already in — it gates every drive that records a person
