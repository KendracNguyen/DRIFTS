# Build and Deploy

**Tonight's milestone.** The firmware has never been compiled. Everything below
assumes that, so the first section is the one that decides how the evening goes.

The repo is an **Arduino sketch**, not PlatformIO and not ESP-IDF:
`Esp32/drifts_esp32/drifts_esp32.ino` with its headers beside it. Do not add a
`platformio.ini` tonight — changing the build system and compiling for the
first time are two unknowns, and you want one.

---

## 1. ESP32-C6 — toolchain

Three of these settings produce confusing failures rather than clear errors.

```bash
arduino-cli config init
arduino-cli config add board_manager.additional_urls \
  https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32          # must resolve to 3.x — C6 support starts at 3.0.0
arduino-cli core list
```

```bash
arduino-cli lib install "NimBLE-Arduino@2.3.9"
arduino-cli lib install "ELMduino"
arduino-cli lib list
```

**NimBLE-Arduino must be 2.3.9 or newer.** Earlier versions crash when
scanning on the C6, which is the first thing this firmware does — so the
symptom is a reboot loop at startup with nothing pointing at the library.

`ELMduino` is used unmodified, through the `Stream` adapter in `ble_stream.h`.
Do not patch it.

### Board options

Do not copy an FQBN from anywhere. Print the real option IDs for your core
version:

```bash
arduino-cli board details --fqbn esp32:esp32:esp32c6
```

Set three things from that list:

| Option | Value | Why |
| --- | --- | --- |
| Flash Size | **8MB** | Default is 4MB and silently wastes half the part |
| Partition Scheme | **8M with spiffs**, or Huge APP | The dual-role BLE build does not fit the default |
| USB CDC On Boot | Enabled | Otherwise the serial console is silent over native USB |

Then build the FQBN from the IDs that command printed, for example:

```bash
arduino-cli compile \
  --fqbn esp32:esp32:esp32c6:FlashSize=8M,PartitionScheme=<id from board details> \
  Esp32/drifts_esp32
```

Leave `MYNEWT_VAL_BLE_MAX_CONNECTIONS` alone. The C6 controller is compiled
with `CONFIG_BT_LE_MAX_CONNECTIONS=3`; raising the host value achieves
nothing, because the host cannot exceed the controller.

### Before you power anything

```bash
python3 Hardware/check_pins.py        # must print: OK: 5 pins agree
cd Esp32/test && make                 # 28 assertions, runs on the laptop
cd Tools && python -m pytest obd_sim/tests -q
```

Confirm nothing is wired to **GPIO 25 or 26**. On the C6 those are SPI flash
lines. `ledcAttach(26, …)` returns `true` and `digitalWrite(25, …)` runs — the
symptom is flash corruption or a boot loop with nothing pointing at the pins.
Motor is **GPIO 10**, buzzer is **GPIO 11**.

### Flash

```bash
arduino-cli upload -p /dev/ttyACM0 --fqbn <same fqbn> Esp32/drifts_esp32
arduino-cli monitor -p /dev/ttyACM0 -c baudrate=115200
```

On Windows the port is `COMn`. If the board is not enumerated, hold BOOT
(GPIO 9) while plugging in.

### Confirming the outputs actually assert

The alert path is driven by LEDC, so a multimeter on a PWM pin reads an
average, not a level. Check it like this:

| Command | Expect on GPIO 10 | Expect on GPIO 11 |
| --- | --- | --- |
| `DROWSY` | ~3.1 V average at 63 % duty, or 3.3 V if `DROWSY_VIB_DUTY` is still 255 | ~2.7 kHz square |
| `STOP` | 0 V | 0 V |

`DROWSY` must **hold** for the full 20 s (`MAX_ALERT_MS`). If it cuts off at
about 3 s you are running old firmware — the fail-safe used to fire when no Pi
had ever connected. Re-pull and reflash.

> `DROWSY_VIB_DUTY` in `config.h` is currently **255** — full 5 V into 3 V
> motors. Either change it to ~160 or fit the 27 Ω series resistors. Whoever
> wires the board and whoever sets that constant have to agree; the mismatch is
> invisible until the motors fail.

---

## 2. Raspberry Pi Zero 2 W

The deploy scripts already exist. Use them rather than installing by hand.

```bash
git clone <repo> ~/drifts && cd ~/drifts/Raspberry
bash deploy/install.sh
```

That script does all of: apt packages (`python3-picamera2`, `python3-opencv`,
`python3-numpy`, `libncnn-dev`, `bluez`), enables Bluetooth, adds the user to
`bluetooth,video,gpio`, creates `.venv` **with `--system-site-packages`**,
installs `requirements.txt`, then templates and enables
`deploy/drifts.service`.

The `--system-site-packages` flag is load-bearing: `picamera2`, `opencv` and
`numpy` come from apt, not pip, and a plain venv cannot see them.

### Verify

```bash
systemctl is-enabled drifts            # enabled
systemctl status drifts                # active (running)
journalctl -u drifts -f
```

Expect in the log:

```
Connected to ESP32 hub
Subscribed to WAKE
Heartbeat started (1.0 s interval)
```

**No heartbeat line means alerts will cut out after 3 seconds.** The ESP32
fail-safe stops the alert if the Pi is silent longer than `FAILSAFE_MS`, and
`heartbeat_s = 1.0` in `config.toml` is what keeps it satisfied.

### Maintenance mode

The unit has `ConditionPathExists=!/boot/firmware/drifts-maintenance`, so it
can be stopped from starting at boot without a shell:

```bash
bash scripts/maint.sh on       # stop auto-start
bash scripts/maint.sh off      # resume
```

Or put an empty file named `drifts-maintenance` on the SD card's boot
partition from any computer. That is the recovery path when the Pi will not
come up and there is no keyboard.

### Finding the ESP32

```bash
python Tools/ble_scan.py       # prints the MAC; put it in config.toml [ble] address
```

Setting `address` skips the name scan and connects measurably faster.

---

## 3. Bring-up order

Do not power everything at once. Each step isolates a failure the next would
hide.

```mermaid
flowchart TD
  A["C6 alone, USB<br/>serial prints at 115200"] --> B["Add MPU-6050<br/>[IMU] MPU-6050 at 0x68"]
  B --> C["Add buzzer<br/>tone audible"]
  C --> D["Add motor branch<br/>bench supply, meter in series"]
  D --> E["Add Pi, own supply first<br/>then combine rails"]
  E --> F["Dongle, full BLE bring-up"]
  style D fill:#fecaca,stroke:#b91c1c
```

Step D is where a missing flyback diode reveals itself cheaply. Confirm the
current matches expectation before connecting the motors to the shared rail —
see `Hardware/falstad/05_motor_driver_no_flyback.txt` for what happens
without it.

At step E, check the 5 V rail does not sag below 4.75 V under simultaneous
load. A rail that dips on motor switch-on is what reboots the Pi mid-demo, and
it looks like a software crash.

---

## 4. What a green build does not prove

- **Inference does not work.** `Raspberry/model/` has no trained weights.
  `detector.py` falls back to a heuristic returning `Open` at 0.85 for every
  input. The Pi answers `NOT_DROWSY` to every WAKE. The 5 Pi tests pass anyway
  because they assert the result is well-formed, not correct.
- **`GATE_BENCH_MODE = true`**, so the 65 km/h operating-domain gate is off and
  the trigger fires parked. No data collected in this state is valid road data.
- **Levels 3 and 4 do not exist.** No voice alert, no rest-stop routing.
