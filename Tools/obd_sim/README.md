# obd_sim — virtual ELM327 dongle

Replaces the `SIM` serial command dropped in the BLE rewrite. Lets the whole
trigger chain be exercised with no car, no dongle and no ignition on.

The firmware is **not modified**. There is no `#ifdef SIMULATION`, no test
hook, no second code path. `obd_ble.cpp` discovers this, probes its GATT
layout, runs the ELM327 dialogue and polls PIDs exactly as it does against a
real Veepeak — so what gets tested is what ships.

Runs on a Linux desktop and on the Pi Zero 2 W.

---

## Quick start

```bash
cd Tools
python -m obd_sim --pty                 # prints the device path, opens the UI
```

Press `1`–`7` to switch scenario while it runs. `q` quits.

```
python -m obd_sim --pty --link /tmp/obd0      # stable path across restarts
python -m obd_sim --pty --scenario rpm        # start in an anomaly
python -m obd_sim --ble                       # advertise to the ESP32
python -m obd_sim --headless --seconds 30     # no UI, for CI
```

`rich` gives the live panel. Without it the tool falls back to one line per
change, which is what you want over ssh anyway.

```bash
pip install rich          # optional, for the UI
pip install bless         # optional, only for --ble
```

---

## Two transports, two jobs

| | What it proves | State |
| --- | --- | --- |
| `--pty` | The ELM327 protocol and the scenarios. Any serial client talks to it | **Tested**, 49 assertions |
| `--ble` | The radio path — the real firmware connecting to a fake dongle | **Never run on hardware** |

### `--pty`

`os.openpty()` gives a device path like `/dev/pts/7`. Point anything at it:

```bash
screen /dev/pts/7 38400
# or
python -c "import serial; s=serial.Serial('/dev/pts/7'); s.write(b'010C\r'); print(s.read(32))"
```

The pty is put in raw mode on both sides. That is not cosmetic: without it the
kernel line discipline echoes every byte back, the client reads its own
commands as replies, and it looks exactly like a dongle stuck in echo mode.
`test_pty.py` asserts it does not happen.

### `--ble`

Advertises as `OBDSIM-DRIFTS` — the name contains `obd`, which matches
`OBD_NAME_HINTS` in `config.h` — serving the HM-10 style **FFE0/FFE1** layout
with one characteristic for both notify and write. That is entry 2 of
`LAYOUTS[]` in `obd_ble.cpp`.

This path is written against the `bless` API and has never been run. It needs
BlueZ ≥ 5.55, an adapter that supports the peripheral role, and no other
process using that adapter as a central. If it fights back, use `--pty` to
prove the protocol and a real dongle to prove the radio. Do not spend an
evening on it.

---

## Scenarios

Every amplitude is chosen against a threshold in
`Esp32/drifts_esp32/config.h`. `tests/test_vehicle.py` simulates a full 30 s
window at 2 Hz and asserts each one crosses the threshold it claims, and that
normal driving crosses nothing.

| Key | Scenario | Targets |
| --- | --- | --- |
| 1 | Normal cruise | nothing — the control case |
| 2 | Parked, idling | the speed gate (open in bench mode, shut in production) |
| 3 | RPM fluctuation | `RPM_STDDEV_TRIGGER = 400` |
| 4 | Throttle fluctuation | `THROTTLE_STDDEV_TRIGGER = 15` |
| 5 | Speed fluctuation | `SPEED_STDDEV_TRIGGER = 8` |
| 6 | Harsh braking | speed path only — see below |
| 7 | Microsleep precursor | all three at once |

**Harsh braking does not test the brake counter.** The firmware takes harsh
braking from the MPU-6050, not from the speed PID, because PID `0x0D` has
1 km/h resolution at ~2 Hz — one quantisation step is about 0.06 g. To
exercise the brake condition, type `BRAKE` three times on the ESP32 serial
console.

The thresholds are **copied** into `vehicle.py`, not imported. `config.h` is
the source of truth; the copies exist so the tests can compare against them.
Change a threshold there and these tests fail, which is the intent.

---

## Tests

```bash
cd Tools && python -m pytest obd_sim/tests -q      # 49 assertions, no hardware
```

Covers protocol framing (CR termination, the `>` prompt, byte-at-a-time
delivery, two commands in one chunk, echo on and off), the full ELMduino
`begin()` sequence, every PID encoding round-trip, `NO DATA` for unsupported
PIDs, a real pty round-trip, and the scenario statistics above.

Not covered: the BLE transport, which needs a radio.

---

## Layout

```
obd_sim/
  __main__.py        CLI, event loop, keyboard
  elm327.py          protocol only — bytes in, bytes out, no I/O
  vehicle.py         state model and scenarios
  ui.py              rich panel, with a plain fallback
  transports/
    pty.py           pseudo-terminal        (tested)
    ble.py           GATT peripheral        (untested)
  tests/
```

The split matters: `elm327.py` touches no clock and no socket, so the parts
most likely to be wrong are the parts testable on a laptop.
