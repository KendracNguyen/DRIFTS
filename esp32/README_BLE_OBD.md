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

Known-good BLE options:

| Dongle | GATT layout | Notes |
| --- | --- | --- |
| Vgate iCar Pro BLE 4.0 | `18F0 / 2AF0 / 2AF1` | Cheap, ~46 ms responses, real sleep mode |
| OBDLink CX | `FFF0 / FFF1 / FFF2` | 247-byte MTU, documented, narrow pairing window |
| Veepeak OBDCheck BLE / BLE+ | usually `FFF0` | Widely available |

Buy two. Clone quality is a lottery.

**Do not connect a phone app directly to the dongle.** A BLE dongle serves one
central at a time — if someone has Torque or Car Scanner open in the background,
the ESP32 silently fails to connect. The phone app connects to the *ESP32*.

## Library and build setup

1. **Arduino-ESP32 core 3.x.** Pin the version and don't upgrade mid-semester;
   NimBLE has broken across core releases before.
2. **NimBLE-Arduino 2.x** (h2zero). Not Bluedroid — it uses ~200 kB less flash
   and handles the dual-role case better.
3. **Raise the connection limit.** In `NimBLE-Arduino/src/nimconfig.h`, uncomment
   and set:
   ```c
   #define MYNEWT_VAL_BLE_MAX_CONNECTIONS 4
   ```
   The default is 3. We need exactly 3 (dongle + Pi + phone), which leaves zero
   headroom for a connection that hasn't torn down yet.
4. **ELMduino** (PowerBroker2), from the Library Manager. Used unmodified —
   `ble_stream.h` makes the BLE characteristic pair look like a `Stream` so
   ELMduino never knows the difference.
5. **Partition scheme: "Huge APP (3MB No OTA/1MB SPIFFS)."** Tools → Partition
   Scheme in the IDE. The dual-role BLE build does not fit the default.

> NimBLE-Arduino 2.x takes scan durations in **milliseconds**; 1.4.x took
> seconds. `obd_ble.cpp` assumes 2.x. If you downgrade, fix `startScan()`.

## Wiring

Only the IMU is wired now — the OBD connection is wireless.

| ESP32 | To |
| --- | --- |
| GPIO 26 | Vibration motor transistor base (keep the flyback diode) |
| GPIO 25 | Buzzer transistor base |
| GPIO 2 | On-board LED, lit when the Pi is subscribed |
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
