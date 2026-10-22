# Wiring Check — Motor, Buzzer, Power

Read this before powering anything wired to GPIO 10 or 11, and work through it
again whenever the alert-chain test (`Docs/alert-chain-test.md`) fails on an
output. Circuits and reference designators are in [`schematic.md`](schematic.md)
and [`BOM.csv`](BOM.csv).

> ⚠ **Do not wire the motor or buzzer to the Raspberry Pi.** They belong on the
> ESP32 (GPIO 10 motor, GPIO 11 buzzer). The Pi has no alert outputs; it talks
> to the ESP32 over Bluetooth. The Pi's only wires are 5 V, GND and the camera
> ribbon.

---

## 0. Our board is not the one in the BOM

`BOM.csv` lists the Espressif **ESP32-C6-DevKitC-1**. The team's board is an
**ESP32-C6-WROOM-1 N8 dev kit with a single USB-C** and a CH343 USB-serial chip.
The firmware GPIO numbers are the same, but **the header pin positions are
not**. Find GPIO 10, 11, 2, 21, 22, 5V, 3V3 and GND **from the silkscreen on
your board**, not from a DevKitC-1 pinout image.

| Signal | GPIO | Notes |
| --- | --- | --- |
| Motor gate | 10 | via 100 Ω to Q1 gate |
| Buzzer gate | 11 | via 100 Ω to Q2 gate |
| Status LED | 2 | via 330 Ω |
| MPU-6050 SDA / SCL | 21 / 22 | 3.3 V only |
| **Never use** | **24 – 30** | SPI flash lines. Writing them corrupts flash, often silently |

`python3 Hardware/check_pins.py` checks the docs against `config.h`; it cannot
check your soldering.

---

## 1. Power-off checks (meter in continuity / resistance mode)

Do every one of these **with all power disconnected**.

| ✔ | Check | Expect | Why it matters |
| --- | --- | --- | --- |
| ☐ | +5 V rail to GND | **Not** a short (reads > 100 Ω, often rising as caps charge) | A short here is usually D1 backwards |
| ☐ | **D1 orientation**: striped (cathode) end on **+5 V**, other end on Q1 drain / motor − | Diode test: ~0.5–0.7 V drain→+5 V, open the other way | Backwards = dead short across the supply at power-on |
| ☐ | Q1 / Q2 gate to GND | ≈ 10 kΩ | Pull-down present. Missing → gate floats during reset → motors twitch on boot |
| ☐ | GPIO 10 to Q1 gate | ≈ 100 Ω | Gate resistor in series |
| ☐ | GPIO 11 to Q2 gate | ≈ 100 Ω | |
| ☐ | Q1 / Q2 source to GND | ≈ 0 Ω | Source must be on ground, not drain |
| ☐ | ESP32 GND to Pi GND to motor-supply GND | ≈ 0 Ω | **Common ground.** Without it the gate drive has no reference |
| ☐ | Nothing on GPIO 24 – 30 | Open | Flash corruption |
| ☐ | GPIO 4 has only the red LED + resistor to GND | No pull-up, nothing else on it | Strapping pin; only an LED is safe there |
| ☐ | Each status LED's long leg (anode) toward its resistor, short leg to GND | Diode test lights it faintly | A reversed LED never lights; the lamp test at power-up shows it |
| ☐ | MPU-6050 VCC on **3V3**, not 5V | | 5 V logic on C6 I2C pins can damage them |

### MOSFET pinout — check the package, not memory

| Part | Package | Pins |
| --- | --- | --- |
| AO3400A | SOT-23 (tiny, 3 legs) | Looking at the marked face, legs down: **1 = G, 2 = S, 3 = D** (single leg on the other side is D) |
| IRLZ44N | TO-220 | Facing the printed side, legs down: **G, D, S** left to right; the metal tab is **D** |
| 2N7002 | SOT-23 | **1 = G, 2 = S, 3 = D** |

Confirm against the datasheet of the exact part you bought; SOT-23 clones
occasionally differ. A swapped source and drain makes the body diode conduct
and the output stays on permanently.

---

## 2. Cautions before first power-on

**⚠ Flyback diode D1 is mandatory on the motor branch.** Without it the
MOSFET drain spikes to roughly 60 V at every turn-off (see
`falstad/05_motor_driver_no_flyback.txt`). An AO3400A is rated 30 V. It will
fail, and can take GPIO 10 with it.

**⚠ D1 current rating.** Four ERMs draw about 0.3–0.5 A together. A **1N4148**
(200 mA continuous) is undersized for the full array. Use a **1N5819**
(Schottky, 1 A) or **1N4001** (1 A). The 1N4148 is fine only for one or two
motors.

**⚠ Motor voltage vs `DROWSY_VIB_DUTY`.** The motors are **3 V** parts.
`config.h` has `DROWSY_VIB_DUTY = 255` (full 5 V). Pick **one**:

- set `DROWSY_VIB_DUTY` to about **160** (≈ 3.1 V average), or
- fit the **27 Ω, 0.5 W** series resistors (`R4`) and leave 255.

**Not both** — that leaves about 1.8 V and the motors may not start, which
looks exactly like a dead driver. The pin test uses the same duty, so it
inherits whichever choice is made. Agree it before the first long test.

**⚠ Buzzer must be PASSIVE.** An active buzzer has its own oscillator; with
DC mode it will sound, with tone mode it may sound wrong or warble. To check:
apply 3–5 V DC directly — a passive piezo only clicks, an active one beeps.

**⚠ Never drive the motor straight from a GPIO.** A C6 pin supplies a few
tens of mA; one ERM wants 60–100 mA. Always through Q1.

**⚠ Bulk capacitor polarity.** C1 (100–470 µF) is electrolytic: stripe = −,
to GND. Backwards electrolytics vent.

**⚠ 5 V rail sag.** Motor switch-on pulls the rail down. If the Pi reboots or
the ESP32 resets the moment the motors start, that is the cause — not
software. Keep C1 close to the motors, keep motor ground returns separate from
the logic ground until the star point, and check the rail stays **≥ 4.75 V**
during the DROWSY test.

**⚠ Mount the MPU-6050 rigidly and flat.** It now measures steering (yaw) as
well as braking. A loose board or soft foam turns vibration into false steering
reversals. Flat = `IMU_YAW_AXIS = 2`; see `Docs/steering-weave.md`.

**⚠ LCD backpack is 5 V — level shifter required.** Never wire the backpack's
SDA/SCL directly to GPIO 21/22: its pull-ups go to 5 V and the C6 pins are
3.3 V only. Before connecting, power the backpack alone and measure SDA and
SCL to GND: if they read ~5 V, they must stay on the HV side of the shifter.
See `schematic.md` §5a.

**⚠ Keep motor wiring away from SDA/SCL.** Switching noise couples into the
MPU-6050 lines and shows up as I2C errors or false harsh-brake events.

---

## 3. Power-on, staged

Power in this order and stop at the first failure. Each step hides the next.

1. **ESP32 alone on USB.** Serial at 115200 prints the banner; DRIFTS-ESP32
   visible in nRF Connect.
2. **Add the buzzer branch** (Q2, piezo). Serial `TEST:0:2000` → 2 s tone.
3. **Add the motor branch with a current-limited bench supply** (set 5 V,
   1 A limit) and a meter in series. Serial `TEST:2000:0` → motors run; current
   ≈ 0.3–0.5 A for four motors. Much more → short or stalled motor; stop.
4. **Then the Pi**, on its own supply first, then the shared rail.
5. **Run the Pi alert-chain test** — `Docs/alert-chain-test.md` §5.

---

## 4. When an output fails the test

| Symptom | Check |
| --- | --- |
| Serial shows `[TEST] Motor GPIO 10 ...` but motor does not run | Meter GPIO 10 → expect ~3.3 V. Present: driver fault (Q1 pins, source not grounded, D1 shorted, motor rail missing). Absent: wrong header pin |
| Motor runs **all the time**, even idle | Q1 source/drain swapped, Q1 failed short (often from missing D1), or gate pull-down missing |
| Motor twitches at power-on only | Gate pull-down missing — harmless but fix it |
| Motor hums but does not spin | Under-voltage: duty 160 **and** series resistors both fitted (§2) |
| Buzzer silent in tone mode, ~1.6 V average on GPIO 11 | Pin is fine → Q2 pins or piezo wiring; or the piezo needs more drive (try 5 V supply side) |
| Buzzer sounds in DC mode but not tone mode | It is an **active** buzzer — replace with passive |
| GPIO 11 reads 0 V in DC mode | Wrong header pin, or firmware older than the `TEST:` commit |
| ESP32 or Pi resets when motors start | Rail sag — §2 |
| Everything worked on USB, fails on 12 V supply | Buck converter / fuse / TVS front end — `schematic.md` §1 |
