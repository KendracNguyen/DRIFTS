# DRIFTS — Falstad circuit simulations

Simulator files for the driver circuits in [`../schematic.md`](../schematic.md).
Reference designators match [`../BOM.csv`](../BOM.csv).

These exist so the hardware team can check the driver circuits *before* parts
are soldered, and so the report can quote simulated numbers next to the
calculated ones.

---

## How to open one

1. Go to **https://www.falstad.com/circuit/circuitjs.html**
2. **File → Import From Text…**
3. Paste the whole contents of one `.txt` file, press OK

Then:

- **Hover** over any element for its instantaneous voltage and current
- **Right-click → View in Scope** to plot a node or a part over time
- The slider panel on the right controls simulation speed — the motor and
  buzzer files run at a small time step, so turn **Simulation Speed** down to
  see individual switching edges

Each file is a plain text netlist. Edit the numbers in a text editor and
re-import — that is faster than clicking through the UI, and the diff is
reviewable.

---

## Files

| File | Circuit | Section in `schematic.md` |
| --- | --- | --- |
| `01_motor_driver.txt` | Q1 + motor array + D1 flyback, PWM duty 63 % | §2 |
| `02_motor_driver_series_resistors.txt` | Four motors with 27 Ω series resistors, full duty | README §5.2 option A |
| `03_buzzer_driver.txt` | **Superseded (rev 2 design).** Q2 + passive piezo, 2.7 kHz. The buzzer is now driven differentially from GPIO 11/18 with no transistor — `schematic.md` §3 | — |
| `04_status_led.txt` | GPIO 2 + 330 Ω + LED | §4 |
| `05_motor_driver_no_flyback.txt` | **Failure case.** D1 removed | §2, the warning |
| `06_power_front_end.txt` | 12 V fuse + TVS clamp + bulk cap, with a load-dump pulse | §1 |

`check_falstad.py` validates the netlists — see *Editing these files* below.

---

## What to look for, and the number to expect

A simulation that agrees with the hand calculation is worth something. One that
disagrees means either the calculation or the circuit is wrong, and that is the
point of running it.

### 01 — motor driver, PWM

| Measure | Expect | Why |
| --- | --- | --- |
| Gate node | 0 V / 3.3 V square, 63 % high | Duty 160/255, the value README §5.2 recommends. `config.h` still holds **255** — if that is not changed, the motors see the full 5 V |
| Q1 drain when on | ≈ 0.15 V | Logic-level FET fully enhanced at 3.3 V |
| Motor current while on | ≈ 0.52 A | 5 V across 9.4 Ω plus Q1 |
| Motor current, average | ≈ 0.33 A | Matches the 400 mA peak line in README §4 |
| D1 current | flows **only** during the off interval | This is the whole reason D1 is there |

The motor current visibly ripples rather than sitting flat: the array's L/R time
constant is ~13 µs against a 50 µs PWM period. That is expected and harmless.

### 02 — series resistors instead of duty capping

| Measure | Expect |
| --- | --- |
| Current per motor | ≈ 76 mA |
| Voltage across each motor | ≈ 2.9 V |
| Power in each 27 Ω resistor | ≈ 0.16 W |

That last number is why `BOM.csv` specifies **0.5 W** series resistors. A
quarter-watt part runs at two-thirds of its rating here and will discolour.

**Pick one approach, 01 or 02, not both.** Running 63 % duty *and* series
resistors gives about 1.8 V at the motors, which is below the start-up voltage
of most coin ERMs — they will sit still and look like a dead driver.

### 03 — buzzer

| Measure | Expect |
| --- | --- |
| Peak current at each edge | ≈ 33 mA |
| Average current | near zero |
| Decay time constant | ≈ 3 µs |

A piezo element is a capacitor, so current flows only on the switching edges.
That is why no flyback diode is needed, and why average current is nothing like
the peak. The 33 mA peak is what README §5.3 means by "near the GPIO's limit" —
it is the reason the buzzer goes through Q2 rather than straight off the pin.

### 04 — status LED

| Measure | Expect |
| --- | --- |
| LED current | ≈ 3.6 mA |
| LED forward drop | ≈ 2.1 V |

README §4 calculates 4 mA. The simulation gives slightly less because the diode
model is exponential rather than a fixed drop. Both are fine; if the LED looks
dim on the bench, drop the resistor to 220 Ω for ~5.5 mA.

### 05 — the failure case ⚠

Open this one next to `01`. It is the same circuit with **D1 deleted**.

Right-click the drain node → View in Scope, then watch a turn-off edge:

| Measure | 01, with D1 | 05, without D1 |
| --- | --- | --- |
| Peak drain voltage | ≈ 5.7 V | **≈ 60 V**, ringing at ~140 kHz |

An AO3400A is rated **30 V** drain-source. An IRLZ44N is rated 55 V. The
unclamped spike exceeds both. This is the single most commonly omitted part in
the whole build and the one that destroys boards, so it is worth watching once.

The spike height here is `I × √(L/C)` and depends on the stray capacitance in
the model, which is a guess. Read the magnitude as "many times the rail", not as
a precise voltage.

### 06 — 12 V front end

The source alternates 12 V and 35 V at 1 Hz, standing in for an ISO 16750-2
load dump.

| Measure | Expect |
| --- | --- |
| Load voltage at 12 V in | ≈ 11.5 V |
| Clamped node during the surge | ≈ 26.6 V, not 35 V |
| Clamp current during the surge | ≈ 15 A |
| Clamp power during the surge | ≈ 400 W |

400 W is inside an SMBJ26A's 600 W rating, which is the check this file exists
to make. Substitute a smaller TVS and the number goes the wrong way.

---

## What these models are not

Stated plainly, because a simulation that looks convincing is more dangerous
than no simulation at all.

- **The ERM motors are an inductor and a resistor.** Real ERMs have back-EMF
  that rises with speed, start-up inrush several times the running current, and
  a stall current higher again. The lumped array is 9.4 Ω / 125 µH, derived from
  the 3 V / 60–100 mA figure in `BOM.csv` — a figure that is still on the open
  items list in README §6. **Re-run `01` once the real part is confirmed.**
- **Falstad's MOSFET has no gate capacitance.** So Rg (100 Ω) does almost
  nothing in the simulation. Its real job is limiting dI/dt into Cgs at the
  switching edge, which this model cannot show. Do not read "Rg does nothing
  here" as "Rg is unnecessary".
- **The gate pull-down also does nothing here**, because an ideal source drives
  the gate at all times. To see what Rpd is for, delete the gate source and
  observe the gate sit at 0 V instead of floating. That is the condition during
  C6 reset.
- **The buck converter is not modelled** in `06`. It appears as a 12 Ω resistor
  representing its input draw at full system load. Switching behaviour, inrush
  and efficiency are all absent.
- **The TVS is a diode clamped to a 26 V rail**, not a real avalanche device. It
  clamps at the right voltage and lets the energy be estimated; it has no
  capacitance, no leakage and no dynamic resistance.
- **The piezo is a capacitor and a resistor.** It has no mechanical resonance,
  so the simulation says nothing about how loud it is at 2.7 kHz.
- **The 5 V rail is ideal.** README §5.4 warns about ground bounce and rail sag
  under motor switching — that is exactly the effect an ideal rail hides. It
  needs a scope on real hardware, not a simulator.

---

## Editing these files

Falstad's importer fails quietly. A wire endpoint one grid step away from an
element's post leaves an open node, and the circuit still loads and still
animates — it just reads zero current, which looks like a design fault rather
than a typo.

After editing any netlist:

```bash
python3 Hardware/falstad/check_falstad.py
```

It checks field counts, flags zero-length elements, and reports any node where
only one post connects. Expect `All 6 netlists pass`.

Coordinates are on a 16 px grid. The MOSFET (`f`) is the one element whose
terminals are not at its endpoints: **gate is the first coordinate pair, and the
drain and source sit 16 px either side of the second pair** — drain on the
`-y` side when the element runs left to right. The checker knows this; hand-
editing a MOSFET without it is how an open node gets introduced.
