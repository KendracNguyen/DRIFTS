# DRIFTS — Recommended Schematic

Companion to `README.md`. Reference designators match the BOM.

---

## 1. Power tree

```
12 V accessory socket (ignition-switched)
  │
  ├─[ 2 A fuse ]──[ TVS SMBJ26A ]──[ 100 µF / 50 V ]
  │                    (clamps load-dump transients)
  │
  └──► Buck converter, 12 V → 5 V @ 3 A
         │
         ├──► Raspberry Pi Zero 2 W   (5 V / GND header pins 2 and 4 / 6)
         │
         ├──► ESP32-C6-DevKitC-1      (5V pin, or USB)
         │       └── on-board LDO ──► 3V3 rail ──► MPU-6050 VCC
         │
         └──► Motor rail (5 V, or 3 V if using series resistors)
```

**For bench and demo work, skip all of this and use a USB power bank.** The
12 V front end only matters once the system lives in a moving vehicle.

The automotive 12 V rail is hostile: ISO 16750-2 specifies load-dump surges to
35 V on modern centrally-suppressed systems. A bare buck module rated 40 V
absolute max will not survive one. The fuse, TVS and bulk capacitance are not
optional garnish.

---

## 2. Vibration motor driver

One MOSFET switching the whole array is sufficient — the firmware drives all
motors together.

```
                              +5 V (or +3 V motor rail)
                                 │
                                 ├──────────────┐
                                 │              │
                              [ M1 ]         [ D1 ]      D1 cathode to +V
                            ERM motors      1N4148       ("stripe end up")
                            4× parallel        │
                                 │              │
                                 └──────┬───────┘
                                        │
                                     ┌──┴──┐
                        GPIO 10 ─────┤ G   │  Q1  AO3400A / IRLZ44N
                              100 Ω  │  D  │      logic-level N-MOSFET
                                     │  S  │
                                     └──┬──┘
                                        │
                         10 kΩ ─────────┤        gate pull-down to GND
                                        │
                                       GND  (motor ground — star point)
```

Component values:

| Part | Value | Purpose |
| --- | --- | --- |
| Q1 | AO3400A (SOT-23) or IRLZ44N (TO-220) | V_GS(th) < 2 V so it fully enhances at 3.3 V |
| D1 | 1N4148 (< 200 mA) or 1N4001 | Flyback. **Omitting this destroys the MOSFET and possibly the C6** |
| R_gate | 100 Ω | Limits inrush into gate capacitance |
| R_pd | 10 kΩ | Holds the gate low while the C6 is in reset and GPIO 10 floats |
| C_bulk | 100–470 µF | Absorbs switch-on inrush; place near the motor rail |
| R_series | see §5.2 in README | Only if dropping 5 V to a 3 V motor |

**Diode orientation is the thing to double-check.** Cathode (striped end) to
the positive rail. Backwards, it is a dead short across the supply the moment
power is applied.

---

## 3. Buzzer driver

```
                              +5 V
                                 │
                             [ LS1 ]   passive piezo
                                 │
                              ┌──┴──┐
                  GPIO 11 ────┤ G   │  Q2  2N7002 / AO3400A
                       100 Ω  │  D  │
                              │  S  │
                              └──┬──┘
                                 │
                   10 kΩ ────────┤
                                 │
                                GND
```

No flyback diode needed — a piezo element is capacitive, not inductive.

`ledcWriteTone(11, 2700)` produces the alert tone. A **passive** element is
required: an active buzzer has its own oscillator and will emit one fixed tone
no matter what frequency the firmware asks for, making every alert tier sound
identical.

---

## 4. Status LED

```
   GPIO 2 ───[ 330 Ω ]───▶|─── GND
                        LED
```

This is an **external** LED. The DevKitC-1's on-board user LED is an
addressable RGB (WS2812-type) on GPIO 8, where `digitalWrite()` does nothing
visible. To use it instead of an external LED, drop this circuit and call
`rgbLedWrite(RGB_BUILTIN, r, g, b)` — but note GPIO 8 is also a strapping pin.

Current: (3.3 − 2.0) / 330 ≈ 4 mA. Adjust the resistor for LED colour and
brightness; red drops ~2.0 V, blue and white ~3.0 V.

---

## 5. MPU-6050

```
   ESP32-C6              MPU-6050 breakout
   ────────              ─────────────────
   3V3     ──────────────  VCC
   GND     ──────────────  GND
   GPIO 21 ──────────────  SDA
   GPIO 22 ──────────────  SCL
                           AD0 ── GND   (selects I2C address 0x68)
```

- **3.3 V only.** Many breakouts have an onboard regulator and tolerate 5 V on
  VCC, but the C6's I2C lines are 3.3 V — never feed 5 V logic to them.
- Most breakouts include **4.7 kΩ pull-ups** on SDA and SCL. Do not add another
  set; paralleled pull-ups over-load the bus.
- Keep the I2C leads short and away from motor wiring. This is the circuit most
  vulnerable to the switching noise described in README §5.4.
- Bus runs at 400 kHz.

---

## 6. What is *not* wired

| Formerly | Now |
| --- | --- |
| SN65HVD230 CAN transceiver → OBD pins 6/14 | Removed. OBD data arrives over BLE from the dongle |
| 12 V tap from OBD pin 16 | Removed. Use the accessory socket (§1) |
| Pi ↔ ESP32 UART | Removed. The link is BLE |

The wired-CAN design is preserved in git history on branch
`NewBrain_David_V1` should the project ever need broadcast-frame data that a
dongle cannot deliver.

---

## 7. Bring-up order

Do not power the whole system at once. Each step isolates a failure the next
would hide.

1. **C6 alone**, USB powered. Confirm it boots and the serial console prints at
   115200.
2. **Add the MPU-6050.** Confirm `[IMU] MPU-6050 at 0x68` on boot. If absent,
   the bus is miswired or the address is wrong.
3. **Add the buzzer.** `BUZZ` via serial should produce a tone. Verify before
   the motors, because it is the quieter failure.
4. **Add the motor branch with a bench supply and a current meter in series.**
   Confirm the current matches expectation before connecting it to the shared
   rail. This is where a missing flyback diode reveals itself cheaply.
5. **Add the Pi**, on its own supply first. Then combine rails and confirm the
   5 V does not sag below 4.75 V under simultaneous load.
6. **Then** the dongle and the full BLE bring-up.

Measure the 5 V rail with a scope at step 5 if one is available. A rail that
dips on motor switch-on is what reboots the Pi mid-demo.
