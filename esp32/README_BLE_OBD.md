# DRIFTS ESP32 — BLE OBD data path

The ESP32 reads vehicle data from a **Bluetooth LE ELM327 dongle** and serves
the Raspberry Pi and the phone app over BLE at the same time. It holds three
BLE connections concurrently: one as a central, two as a peripheral.

```
BLE ELM327 dongle ──(central)──► ESP32 ──(peripheral)──► Raspberry Pi Zero 2 W
                                   └────(peripheral)──► phone app
```

## Buy the right dongle

**It must be Bluetooth LE, not Bluetooth Classic.** Most cheap "ELM327 v1.5/v2.1"
clones on Amazon are Classic SPP only and will not work with any of this code.
ESP32-S3, C3, C6 and H2 have no Classic Bluetooth at all, so on those chips a
Classic dongle is simply a brick.

Known-good BLE options, in buying order:

| Dongle | GATT layout | Approx | Notes |
| --- | --- | --- | --- |
| **Veepeak OBDCheck BLE+** | `FFE0/FFE1` or `FFF0/FFF1/FFF2` | $30 | Proven from an ESP32 in a public project; both layouts already in `LAYOUTS[]` |
| **Vgate iCar Pro BLE 4.0** | `18F0 / 2AF0 / 2AF1` | $25 | Different layout on purpose; real sleep mode; ~46 ms responses |
| OBDLink CX | `FFF0 / FFF1 / FFF2` | $60 | 247-byte MTU, best documented, but a narrow pairing window |

**Buy two, of different brands.** Two of the same model share a batch; two
layouts mean a failure tells you something.

Naming traps that cost a week:

- Vgate sells a **combined listing** titled "iCar Pro (BT3.0 / BLE4.0)". BT3.0
  is Classic and is useless on a C6. Check the variant, not the title.
- Veepeak **"BLE"** and **"BLE+"** are different products. Take the BLE+; the
  plain BLE has documented connection drops, and this one shares a radio with
  two other links.
- Avoid: OBDLink MX+ (Bluetooth 3.0), Veepeak Mini, BAFX, and every $8 blue
  "ELM327 v1.5/v2.1" clone. All Classic.

When the dongle arrives, connect to it with **nRF Connect** on a phone and read
its actual service and characteristic UUIDs before flashing anything. The
firmware probes four known layouts and falls back to any service with exactly
one notify and one write characteristic, so it will probably just work — but
thirty seconds with nRF Connect turns a failed connection from a mystery into
a value you paste into `LAYOUTS[]`.

**Do not connect a phone app directly to the dongle.** A BLE dongle serves one
central at a time — if someone has Torque or Car Scanner open in the background,
the ESP32 silently fails to connect. The phone app connects to the *ESP32*.

## Target board: ESP32-C6-WROOM-1

The firmware targets the **C6**, not the original ESP32-WROOM-32. This is not
a cosmetic difference:

- **No Classic Bluetooth.** The C6 has BLE only. A Bluetooth Classic ELM327
  dongle cannot work on this hardware at all, which is why the BLE dongle is
  mandatory rather than preferred.
- **Single core, 160 MHz.** The BLE controller, the BLE host and `loop()` share
  one core. Keep `loop()` non-blocking — the "ESP32 does I/O, Pi does scoring"
  split is load-bearing here, not just tidy.
- **Three BLE connections is a hard ceiling.** The prebuilt Arduino controller
  is compiled with `CONFIG_BT_LE_MAX_CONNECTIONS=3`, and we need exactly three.
  Do not raise `MYNEWT_VAL_BLE_MAX_CONNECTIONS` above 3 — the host cannot
  exceed the controller. This means disconnect cleanup must be reliable: one
  stale link blocks the third connection.
- **Different pins.** See the warning at the top of `config.h`. GPIO 25 and 26
  are SPI flash lines on the C6 and using them corrupts flash without any
  error message.

## Library and build setup

1. **Arduino-ESP32 core 3.x** (C6 support starts at 3.0.0; use the latest
   3.3.x). Pin the version and don't upgrade mid-semester.
2. **Board: "ESP32C6 Dev Module"**, FQBN `esp32:esp32:esp32c6`.
3. **NimBLE-Arduino 2.3.9 minimum**, 2.5.0 preferred. Versions before 2.3.9
   have a documented **crash when scanning on the C6** — which is exactly what
   dongle discovery does. 2.3.0 is the first version with C6 support at all;
   2.3.3 fixed multiple-definition errors on core 3.3+, and 2.3.8 fixed a
   deinit crash and an init/deinit memory leak on C6.
4. **Leave `MYNEWT_VAL_BLE_MAX_CONNECTIONS` at its default of 3.** (On the
   original ESP32 you would raise it to 4 for headroom; on the C6 the
   controller caps at 3, so raising the host achieves nothing.)
5. **ELMduino** (PowerBroker2), from the Library Manager. Used unmodified —
   `ble_stream.h` makes the BLE characteristic pair look like a `Stream` so
   ELMduino never knows the difference.
6. **Flash size 8MB** on a DevKitC-1 (the default menu setting is 4MB and
   wastes half the part), **partition scheme `default_8MB`** — or `huge_app`
   on a 4MB board. The dual-role BLE build does not fit the default partition.

> NimBLE-Arduino 2.x takes scan durations in **milliseconds**; 1.4.x took
> seconds. `obd_ble.cpp` assumes 2.x. If you downgrade, fix `startScan()`.

## Wiring

Only the IMU is wired now — the OBD connection is wireless.

| ESP32 | To |
| --- | --- |
| GPIO 10 | Vibration motor transistor base (keep the flyback diode) |
| GPIO 11 | Buzzer transistor base |
| GPIO 2 | External status LED, lit when the Pi is subscribed |
| GPIO 21 | MPU-6050 SDA |
| GPIO 22 | MPU-6050 SCL |

The SN65HVD230 CAN transceiver is no longer used. The wired-CAN design is kept
in git history on `NewBrain_David_V1` as the fallback if the fatigue metrics
ever need more than a dongle can deliver.

**Set the IMU orientation before trusting anything.** `IMU_LONG_AXIS` and
`IMU_LONG_SIGN` in `imu.h` decide which axis points along the vehicle and which
direction is braking. Get them wrong and hard braking registers as hard
acceleration. Verify once on a test drive: brake firmly, confirm the `[IMU]
Harsh brake` line appears.

## What to expect from the data

A BLE ELM327 delivers roughly **6–11 request/response cycles per second in
total**, shared across every PID. With four PIDs in rotation that is about
**1.5–2.75 Hz per signal**. The firmware measures what it actually achieves and
reports it as `hz` in the telemetry and in `STATUS` — log that number rather
than assuming one, because modern vehicle gateways throttle OBD deliberately
and the rate is vehicle-specific.

That rate is fine for 30–60 second rolling standard deviations of RPM, throttle
and speed. It is **not** enough to differentiate speed into acceleration:
PID `0x0D` has 1 km/h resolution, so at ~2 Hz one quantisation step is about
0.06 g and jerk is pure noise. Harsh braking therefore comes from the MPU-6050,
which is why the IMU moved onto the ESP32.

## Serial commands (115200 baud)

| Command | Effect |
| --- | --- |
| `WAKE` | Trigger a Pi analysis cycle by hand |
| `DROWSY` | Simulate a DROWSY result from the Pi |
| `NOT_DROWSY` | Simulate a NOT_DROWSY result |
| `STOP` | Stop the alert |
| `BRAKE` | Inject one harsh-brake event |
| `STATUS` | Print OBD state, achieved Hz, all window statistics, link states |

## Bench bring-up order

Do these in order; each one isolates a failure the next would hide.

1. **Dongle link only, on a desk, dongle unplugged from any car.** You should
   see `Scanning` → `Candidate` → `Connected` → a matched layout. If the layout
   is not matched, the serial log lists every service and its notify/write
   counts — add the UUIDs to `LAYOUTS[]` in `obd_ble.cpp`.
2. **Dongle in a car, ignition on.** `STATUS` should show `obd=polling` and a
   nonzero `hz`. If `hz` is 0 but the state is `polling`, the dongle is reachable
   but the vehicle is not answering — check the ignition, then the protocol
   setting (`ELM_PROTOCOL` in `config.h`).
3. **Check `ovf` stays at 0** in `STATUS`. A nonzero receive-overflow count means
   responses were dropped and any decode from them is suspect.
4. **Verify the IMU sign** as described above.
5. **Only then** connect the Pi and the phone, and confirm all three links stay
   up together for ten minutes.

## Known limits, stated honestly

- **The operating-domain gate suppresses the trigger below 65 km/h.** Vehicle-
  dynamics fatigue metrics are only valid on open road; below that the system
  refuses to score rather than firing on stop-and-go traffic. This means
  **parking-lot testing cannot exercise the trigger.** Use the `BRAKE` and
  `WAKE` serial commands to test the downstream chain, and get highway miles
  for the trigger itself.
- The IMU bias is calibrated once at startup, stationary. On a slope that bakes
  in a gravity component. A production system would estimate pitch continuously.
- `NO DATA` is the ordinary reply to a PID the vehicle does not support. It is
  counted, not treated as a fault.
- Reconnecting to the dongle re-runs the full ELM init, because clones without
  working memory redo the protocol search every time.

## Tests

The logic that does not need an ESP32 is tested on the host:

```bash
cd esp32/test && make
```

This covers the rolling-window statistics (window length, stale-sample
eviction, standard deviation, the speed gate, cooldown, rising-edge firing)
and the BLE stream framing (CR-terminated writes, chunking, notification
reassembly, ring-buffer wrap, overflow accounting). The NimBLE and ELMduino
layers are not covered and must be tested on hardware.
