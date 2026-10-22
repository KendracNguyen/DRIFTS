"""Draw the DRIFTS system schematic (KiCad-style look, not a KiCad file).

    pip install matplotlib
    python Hardware/drawings/draw_schematic.py

Writes DRIFTS_Schematic_rev3.pdf and .png next to this script. Edit the
coordinates below and re-run to make the next revision. Units are 0.1 inch;
y grows downward.
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Polygon, Rectangle

REV = 3
W, H = 240, 202

WIRE = "#1a6b1a"
EDGE = "#7a1010"
FILL = "#fff4d4"
FRAME = "#888888"
BLE = "#6a2ca0"

fig = plt.figure(figsize=(W / 10, H / 10))
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, W)
ax.set_ylim(H, 0)
ax.axis("off")


# ---------------------------------------------------------------- primitives
def wire(*pts, color=WIRE, lw=1.6, ls="-"):
    xs, ys = zip(*pts)
    ax.plot(xs, ys, color=color, lw=lw, ls=ls, solid_capstyle="round", zorder=2)


def dot(x, y):
    ax.add_patch(Circle((x, y), 0.45, color=WIRE, zorder=4))


def text(x, y, s, size=8, color="black", ha="left", va="center", weight="normal", style="normal"):
    ax.text(x, y, s, fontsize=size, color=color, ha=ha, va=va, weight=weight, style=style, zorder=5)


def frame(x, y, w, h, title):
    ax.add_patch(Rectangle((x, y), w, h, fill=False, ec=FRAME, ls=(0, (6, 4)), lw=1.3, zorder=1))
    text(x + 1.5, y + 2.2, title, size=9, color="#444", weight="bold")


def box(x, y, w, h, title, left=(), right=(), title_lines=1):
    """Component body. left/right: [(name, y)]. Returns {name: (x, y)} pin points."""
    ax.add_patch(Rectangle((x, y), w, h, fc=FILL, ec=EDGE, lw=1.8, zorder=3))
    text(x + w / 2, y + 1.2 + title_lines * 0.9, title, size=7.5, color=EDGE, ha="center", weight="bold")
    pins = {}
    for name, py in left:
        text(x + 1, py, name, size=7.5)
        pins[name] = (x, py)
    for name, py in right:
        text(x + w - 1, py, name, size=7.5, ha="right")
        pins[name] = (x + w, py)
    return pins


def gnd(x, y, drop=2.0):
    wire((x, y), (x, y + drop), color=EDGE, lw=1.4)
    for i, half in enumerate((2.2, 1.4, 0.6)):
        yy = y + drop + i * 0.8
        ax.plot([x - half, x + half], [yy, yy], color=EDGE, lw=1.6, zorder=3)


def flag(x, y, label, rise=2.5):
    wire((x, y), (x, y - rise), color=EDGE, lw=1.4)
    ax.plot([x - 1.6, x + 1.6], [y - rise, y - rise], color=EDGE, lw=2, zorder=3)
    text(x, y - rise - 1.4, label, size=7.5, color=EDGE, ha="center", weight="bold")


def res_h(x1, y, label, below=False):
    """Horizontal resistor body from x1 to x1+6."""
    ax.add_patch(Rectangle((x1, y - 1), 6, 2, fc=FILL, ec=EDGE, lw=1.4, zorder=3))
    text(x1 + 3, y + (2.4 if below else -2.2), label, size=7.5, ha="center")


def res_v(x, y1, label):
    ax.add_patch(Rectangle((x - 1, y1), 2, 6, fc=FILL, ec=EDGE, lw=1.4, zorder=3))
    text(x + 2, y1 + 3, label, size=7.5)


def cap_v(x, y, label, polar=False):
    """Capacitor centred at (x, y), plates horizontal."""
    for yy in (y - 0.6, y + 0.6):
        ax.plot([x - 2, x + 2], [yy, yy], color=EDGE, lw=2.4, zorder=3)
    if polar:
        text(x - 3, y - 1.8, "+", size=8, color=EDGE)
    text(x + 2.6, y, label, size=7)


def led_h(x, y, color, label):
    """LED from x to x+4, anode left."""
    ax.add_patch(Polygon([(x, y - 1.4), (x, y + 1.4), (x + 3, y)], closed=True, fc=color, ec=EDGE, lw=1.3, zorder=3))
    ax.plot([x + 3, x + 3], [y - 1.4, y + 1.4], color=EDGE, lw=1.6, zorder=3)
    for dx in (0.6, 1.8):
        ax.annotate("", xy=(x + dx + 1.6, y - 3.2), xytext=(x + dx + 0.4, y - 1.6),
                    arrowprops=dict(arrowstyle="->", color=EDGE, lw=1))
    text(x + 1.5, y + 2.6, label, size=7.5, ha="center")


def no_connect(x, y):
    ax.plot([x - 0.9, x + 0.9], [y - 0.9, y + 0.9], color=EDGE, lw=1.6, zorder=4)
    ax.plot([x - 0.9, x + 0.9], [y + 0.9, y - 0.9], color=EDGE, lw=1.6, zorder=4)


# ---------------------------------------------------------------- title
text(W / 2, 3, f"DRIFTS — Schematic (NewBrain architecture) · rev {REV}", size=16, ha="center", weight="bold")
text(W / 2, 6.2, "Team 1 · ESP32-C6 dev kit + Raspberry Pi Zero 2 W · rev 3 adds status LEDs ×3, "
     "16x2 I2C LCD and I2C level shifter · generated drawing (not a KiCad file)",
     size=8.5, ha="center", style="italic")

# ---------------------------------------------------------------- power input
frame(2, 10, 100, 30, "Power input (vehicle)")
j1 = box(5, 14, 16, 20, "J1\n12V acc.", right=[("+12V", 20), ("GND", 30)], title_lines=2)
wire(j1["+12V"], (26, 20))
res_h(26, 20, "F1  2 A")
wire((32, 20), (72, 20))
wire(j1["GND"], (24, 30))
gnd(24, 30)
# D2 TVS, cathode up
dot(42, 20)
wire((42, 20), (42, 24.5), color=EDGE, lw=1.4)
ax.add_patch(Polygon([(40.4, 28), (43.6, 28), (42, 24.8)], closed=True, fc=FILL, ec=EDGE, lw=1.4, zorder=3))
ax.plot([40.2, 43.8], [24.8, 24.8], color=EDGE, lw=1.6, zorder=3)
ax.plot([40.2, 39.6], [24.8, 24.0], color=EDGE, lw=1.6, zorder=3)
ax.plot([43.8, 44.4], [24.8, 25.6], color=EDGE, lw=1.6, zorder=3)
wire((42, 28), (42, 31), color=EDGE, lw=1.4)
gnd(42, 31)
text(35.5, 26.5, "D2\nSMBJ26A\n(TVS)", size=7, ha="center")
# C1
dot(54, 20)
wire((54, 20), (54, 25.4), color=EDGE, lw=1.4)
cap_v(54, 26, "C1\n100µF/50V", polar=True)
wire((54, 26.6), (54, 31), color=EDGE, lw=1.4)
gnd(54, 31)
u6 = box(72, 13, 22, 22, "U6 HW-319A\nLM2596 buck", left=[("IN+", 20), ("IN-", 30)],
         right=[("OUT+", 20), ("OUT-", 30)], title_lines=2)
wire(u6["IN-"], (66, 30))
gnd(66, 30)
wire(u6["OUT+"], (98, 20))
flag(98, 20, "+5V")
wire(u6["OUT-"], (98, 30))
gnd(98, 30)

# ---------------------------------------------------------------- vision + BLE
frame(106, 10, 132, 30, "Vision and wireless (off-board modules)")
u5 = box(110, 16, 18, 20, "U5\nOV5647", right=[("CSI", 30)], title_lines=2)
u2 = box(146, 13, 30, 24, "U2 Pi Zero 2 W", left=[("5V", 18), ("GND", 24), ("CSI", 30)])
wire(u5["CSI"], u2["CSI"])
text(137, 28.6, "CSI-2 ribbon", size=7, ha="center")
wire(u2["5V"], (140, 18))
flag(140, 18, "+5V")
wire(u2["GND"], (136, 24))
gnd(136, 24)
u3 = box(196, 22, 22, 12, "U3 ELM327\nBLE OBD dongle", title_lines=2)
text(207, 31.5, "Veepeak (BLE, not Classic)", size=6.5, ha="center", style="italic")

# ---------------------------------------------------------------- ESP32-C6
u1 = box(92, 48, 18, 56, "U1 ESP32-C6\nWROOM-1 N8 dev kit",
         left=[("5V", 54), ("3V3", 62), ("GND", 70), ("GPIO22", 80), ("GPIO21", 88)],
         right=[("GPIO10 PWM", 56), ("GPIO11 tone", 64), ("GPIO2", 72), ("GPIO3", 78), ("GPIO4", 84)],
         title_lines=2)
wire(u1["5V"], (88.5, 54))
flag(88.5, 54, "+5V")
# BLE links
wire((101, 48), (161, 37), color=BLE, lw=2, ls=(0, (5, 3)))
wire((106, 48), (207, 34), color=BLE, lw=2, ls=(0, (5, 3)))
text(128, 41.5, "BLE", size=8, color=BLE, weight="bold")
text(160, 41.5, "BLE", size=8, color=BLE, weight="bold")

# ---------------------------------------------------------------- I2C bus: sensors + display
frame(2, 44, 84, 104, "I2C bus · sensors and display (100 kHz)")
u4 = box(24, 56, 16, 38, "U4 GY-521\nMPU-6050",
         left=[("AD0", 62), ("XDA", 70), ("XCL", 80), ("INT", 88)],
         right=[("VCC", 62), ("GND", 70), ("SCL", 80), ("SDA", 88)], title_lines=2)
wire(u4["AD0"], (18, 62))
gnd(18, 62)
for p in ("XDA", "XCL", "INT"):
    wire(u4[p], (19, u4[p][1]))
    no_connect(19, u4[p][1])
wire(u4["VCC"], u1["3V3"])
dot(50, 62)
flag(50, 62, "+3V3")
wire(u4["GND"], u1["GND"])
dot(56, 70)
gnd(56, 70)
wire(u4["SCL"], u1["GPIO22"])
wire(u4["SDA"], u1["GPIO21"])
text(62, 78.6, "SCL", size=7)
text(62, 86.6, "SDA", size=7)
text(26, 97, "XDA / XCL / INT unused (NC) · AD0 to GND = 0x68", size=6.5, style="italic")

# U7 level shifter: HV side left (toward the LCD), LV side right (toward U1)
u7 = box(50, 104, 18, 26, "U7 level shifter\nBSS138 2-ch",
         left=[("HV", 109), ("HV1", 115), ("HV2", 121), ("GND ", 127)],
         right=[("LV", 109), ("LV1", 115), ("LV2", 121), ("GND", 127)], title_lines=2)
dot(78, 88)
wire((78, 88), (78, 115), u7["LV1"])
dot(82, 80)
wire((82, 80), (82, 121), u7["LV2"])
wire(u7["LV"], (73, 109))
flag(73, 109, "+3V3")
wire(u7["GND"], (73, 127))
gnd(73, 127)

# U8 LCD + backpack
u8 = box(6, 102, 24, 32, "U8 LCD 1602 HD44780\n+ PCF8574 backpack\n0x27 / 0x3F",
         right=[("VCC", 109), ("SDA", 115), ("SCL", 121), ("GND", 127)], title_lines=3)
wire(u8["VCC"], u7["HV"])
dot(40, 109)
flag(40, 109, "+5V")
wire(u8["SDA"], u7["HV1"])
wire(u8["SCL"], u7["HV2"])
wire(u8["GND"], u7["GND "])
dot(40, 127)
gnd(40, 127)
text(8, 132, "contrast pot + backlight jumper on backpack", size=6.5, style="italic")
text(33, 100.5, "5 V side", size=7, color="#555", ha="center")
text(64, 100.5, "3.3 V side", size=7, color="#555", ha="center")
text(4, 140, "⚠ U8 pulls SDA/SCL up to 5 V: never wire it straight to GPIO21/22.", size=7.5, color="#b00020", weight="bold")
text(4, 143.5, "MPU-6050 stays on the 3.3 V side. One pull-up pair per side; add none.", size=7.5, color="#b00020")

# ---------------------------------------------------------------- motors
frame(124, 44, 76, 40, "Vibration motor modules ×4 (onboard driver)")
wire(u1["GPIO10 PWM"], (130, 56))
res_h(130, 56, "R1  100Ω")
wire((136, 56), (152, 56), (152, 64), (165, 64))
dot(144, 56)
wire((144, 56), (144, 62))
res_v(144, 62, "R2\n10kΩ")
wire((144, 68), (144, 72))
gnd(144, 72)
text(158, 62.6, "MOTOR_IN", size=6.5, ha="center")
for k in (3, 2, 1):
    ax.add_patch(Rectangle((165 + k * 1.2, 46 + k * 1.2), 20, 24, fc=FILL, ec=EDGE, lw=1.3, zorder=3))
m = box(165, 46, 20, 24, "M1–M4\n(×4)", left=[("GND", 52), ("VCC", 58), ("IN", 64)], title_lines=2)
wire(m["VCC"], (156, 58), (156, 50))
flag(156, 50, "+3V3")
wire(m["GND"], (161, 52))
gnd(161, 52, drop=1.4)

# ---------------------------------------------------------------- buzzer
frame(124, 88, 76, 42, "Buzzer driver")
wire(u1["GPIO11 tone"], (121, 64), (121, 112), (128, 112))
res_h(128, 112, "R3  100Ω")
wire((134, 112), (158, 112))
dot(142, 112)
wire((142, 112), (142, 116))
res_v(142, 116, "R4\n10kΩ")
wire((142, 122), (142, 125))
gnd(142, 125)
# Q2 n-MOSFET
ax.plot([158, 158], [108, 116], color=EDGE, lw=2, zorder=3)
for yy in (108.5, 112, 115.5):
    ax.plot([159.2, 159.2], [yy - 1.1, yy + 1.1], color=EDGE, lw=2, zorder=3)
ax.plot([159.2, 164], [108.5, 108.5], color=EDGE, lw=1.4, zorder=3)
ax.plot([159.2, 164], [115.5, 115.5], color=EDGE, lw=1.4, zorder=3)
ax.annotate("", xy=(159.4, 112), xytext=(164, 112), arrowprops=dict(arrowstyle="-|>", color=EDGE, lw=1.2))
ax.plot([164, 164], [112, 115.5], color=EDGE, lw=1.4, zorder=3)
wire((164, 108.5), (164, 102.5))
wire((164, 115.5), (164, 124))
gnd(164, 124)
text(166, 112, "Q2\n2N7002 / AO3400A", size=7.5)
text(165.2, 107, "D", size=6.5, color=EDGE)
text(165.2, 118, "S", size=6.5, color=EDGE)
text(155, 110.4, "G", size=6.5, color=EDGE)
# LS1
ax.add_patch(Circle((164, 99.5), 3, fc=FILL, ec=EDGE, lw=1.6, zorder=3))
text(164, 99.5, "~", size=11, color=EDGE, ha="center")
wire((164, 96.5), (164, 94))
flag(164, 94, "+5V")
text(168.5, 98.5, "LS1  piezo, tone 2.7 kHz\nTMB12A03: confirm PASSIVE (note 7)", size=7.5)

# ---------------------------------------------------------------- status LEDs
frame(124, 134, 76, 26, "Status LEDs ×3")
rows = [
    ("GPIO2", 119, 142, "R5  100Ω", "#3ccf5a", "D3 green", "connections"),
    ("GPIO3", 117, 148, "R6  330Ω", "#ffd60a", "D4 yellow", "caution 50–69 / system"),
    ("GPIO4", 115, 154, "R7  330Ω", "#ff4d4d", "D5 red", "drowsy (LED only)"),
]
for pin, xv, yr, rl, col, dl, meaning in rows:
    wire(u1[pin], (xv, u1[pin][1]), (xv, yr), (130, yr))
    res_h(130, yr, rl, below=False)
    wire((136, yr), (144, yr))
    led_h(144, yr, col, "")
    wire((147, yr), (156, yr))
    if yr != 154:
        dot(156, yr)
    text(158.5, yr - 0.3, f"{dl} — {meaning}", size=7.5)
wire((156, 142), (156, 154), (156, 155.5))
gnd(156, 155.5, drop=0.6)

# ---------------------------------------------------------------- decoupling
frame(204, 44, 34, 40, "Decoupling")
for x, lab, val, pol in ((210, "+3V3", "C3\n0.1µF\n(104)", False),
                         (222, "+5V", "C4\n0.1µF\n(104)", False)):
    flag(x, 53, lab)
    wire((x, 53), (x, 57.4), color=EDGE, lw=1.4)
    cap_v(x, 58, val, polar=pol)
    wire((x, 58.6), (x, 63), color=EDGE, lw=1.4)
    gnd(x, 63)
flag(210, 72, "+3V3")
wire((210, 72), (210, 74.4), color=EDGE, lw=1.4)
cap_v(210, 75, "C2  220µF (bulk)", polar=True)
wire((210, 75.6), (210, 77), color=EDGE, lw=1.4)
gnd(210, 77, drop=1)

# ---------------------------------------------------------------- notes
ax.plot([2, W - 2], [164, 164], color="#aaa", lw=1)
text(2, 167, "NOTES", size=10, weight="bold")
notes = [
    [
        "1. U6 = HW-319A-V7.0.1 LM2596 module. Trim the output to 5.0 V with the",
        "   trimmer BEFORE connecting the Pi or C6. Bench/demo: skip the 12 V",
        "   front end and use a USB power bank.",
        "2. Motor modules M1–M4 (GND, VCC, IN) have an onboard driver. IN ← GPIO10",
        "   (20 kHz PWM, 8-bit) via R1 100Ω; R2 10k holds IN low during reset.",
        "3. Module VCC drawn from +3V3. Verify the module rating and that the C6",
        "   LDO can supply 4 × 60–100 mA plus the IMU; +5V is the alternative.",
        "4. U4 GY-521: AD0→GND = 0x68. Wire.begin(21, 22). Breakout has pull-ups.",
    ],
    [
        "5. I2C runs at 100 kHz (PCF8574 limit), shared by U4 and U8.",
        "   U7 BSS138 shifter: LV = +3V3, HV = +5V. U8 backpack is a 5 V part",
        "   and pulls SDA/SCL to 5 V: never connect it directly to the C6.",
        "   U8 address 0x27 (PCF8574) or 0x3F (PCF8574A), auto-detected.",
        "6. LEDs: D3 green GPIO2 (100Ω true green, 330Ω yellow-green), D4 yellow",
        "   GPIO3, D5 red GPIO4. ~12 mA total. GPIO4 is a strapping pin: an LED",
        "   to GND only, nothing else. Mount LEDs and LCD in the passenger's view.",
        "7. LS1 must be PASSIVE for tone (GPIO11 → Q2). The fitted TMB12A03 may",
        "   be ACTIVE: check with TEST:0:3000:DC. No flyback diode for a piezo.",
    ],
    [
        "8. C3/C4: 0.1µF ceramic at +3V3 and +5V near U1; one per module VCC.",
        "9. Wireless (dashed purple): Pi↔C6 and OBD dongle↔C6 over BLE. U3",
        "   must be BLE, not Classic. Camera U5 connects to the Pi only.",
        "10. Do not use: 5, 8, 9, 15 (strapping); 12, 13 (USB); 16, 17 (UART0",
        "   to CH343); 14, 24–30 (flash). GPIO4 = red LED only.",
        "11. Same-named flags (+5V, +3V3, GND) are one net, as in KiCad.",
        "12. Firmware v12: LCD shows INTERVENTION 1/2 (score 50–69 / <50),",
        "   same bands as the LEDs. See Docs/lcd-display.md, Hardware/pinout.md.",
    ],
]
for col, lines in enumerate(notes):
    for i, line in enumerate(lines):
        text(2 + col * 80, 171 + i * 3.1, line, size=7.5)

out = Path(__file__).resolve().parent / f"DRIFTS_Schematic_rev{REV}"
fig.savefig(out.with_suffix(".pdf"))
fig.savefig(out.with_suffix(".png"), dpi=110)
print(f"wrote {out}.pdf and .png")
