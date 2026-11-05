"""Draw Hardware/pinout.svg: every wire to the ESP32-C6 (firmware v21).

    python Hardware/drawings/draw_pinout.py

Plain SVG, no dependencies. Edit PINS below when the wiring changes, then
re-run; check that config.h agrees with `python3 Hardware/check_pins.py`.
"""

from pathlib import Path
from xml.sax.saxutils import escape

FW = "v21"

# (board label, wire colour, goes to, through, group)
PINS = [
    ("5V", "#d32f2f", "motor module VCC (5 V modules)", "5 V rail", "power"),
    ("3V3", "#f57c00", "MPU-6050 VCC · motor module VCC (3.3 V modules)", "3.3 V rail", "power"),
    ("GND", "#212121", "every part: one common ground", "", "power"),
    ("GPIO 10", "#6a1b9a", "IN of the 4 vibration motor modules", "R1 100 Ω · R2 10 kΩ to GND", "out"),
    ("GPIO 11", "#00838f", "Piezo LS1 leg 1 (5 kHz)", "R6 100 Ω", "out"),
    ("GPIO 18", "#00838f", "Piezo LS1 leg 2 (same tone, inverted)", "R7 100 Ω", "out"),
    ("GPIO 21", "#1565c0", "MPU-6050 SDA (I2C)", "direct (3.3 V part)", "i2c"),
    ("GPIO 22", "#1565c0", "MPU-6050 SCL (I2C)", "direct (3.3 V part)", "i2c"),
    ("GPIO 19", "#2e7d32", "Pi Zero GPIO 15 RX (pin 10): UART TX", "R10 1 kΩ", "uart"),
    ("GPIO 20", "#2e7d32", "Pi Zero GPIO 14 TX (pin 8): UART RX", "R11 1 kΩ", "uart"),
]

ROW = 46
TOP = 96
W = 1180
H = TOP + ROW * len(PINS) + 230


def t(x, y, s, size=14, fill="#222", anchor="start", weight="normal"):
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" '
            f'font-weight="{weight}">{escape(s)}</text>')


def main() -> None:
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           'font-family="Helvetica, Arial, sans-serif">',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           t(W / 2, 36, f"DRIFTS — ESP32-C6 pinout (firmware {FW}, schematic rev 7)", 22, anchor="middle", weight="bold"),
           t(W / 2, 60, "Find pins by the label printed on the board, not by position. "
                        "Same-named power tags are the same wire.", 13, "#555", anchor="middle")]
    bx, bw = 40, 190
    by, bh = TOP - 26, ROW * len(PINS) + 76
    out.append(f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="12" fill="#1f3b5c" stroke="#0f2236" stroke-width="2"/>')
    out.append(t(bx + bw / 2, by + bh - 40, "ESP32-C6", 18, "#fff", "middle", "bold"))
    out.append(t(bx + bw / 2, by + bh - 20, "WROOM-1 N8 dev kit", 13, "#cfe0f2", "middle"))
    for i, (label, col, to, via, _g) in enumerate(PINS):
        y = TOP + i * ROW
        px = bx + bw
        out.append(t(px - 14, y + 5, label, 14, "#fff", "end", "bold"))
        out.append(f'<circle cx="{px}" cy="{y}" r="6" fill="#f2c94c" stroke="#8a6d1f"/>')
        out.append(f'<line x1="{px + 6}" y1="{y}" x2="{px + 150}" y2="{y}" stroke="{col}" stroke-width="4" stroke-linecap="round"/>')
        if via:
            out.append(f'<rect x="{px + 160}" y="{y - 13}" width="250" height="26" rx="6" fill="#fff7e0" stroke="#c9a227"/>')
            out.append(t(px + 285, y + 5, via, 12.5, "#5c4400", "middle"))
            out.append(f'<line x1="{px + 410}" y1="{y}" x2="{px + 440}" y2="{y}" stroke="{col}" stroke-width="4" stroke-linecap="round"/>')
        out.append(t(px + 450, y + 5, to, 14))
    y = TOP + ROW * len(PINS) + 76
    notes = [
        "Do not use: 4, 5, 8, 9, 15 (strapping) · 6, 7 (JTAG) · 12, 13 (USB) · 16, 17 (UART0 serial monitor) · "
        "14, 24–30 (flash: corrupts it silently).",
        "Free: GPIO 2, 3, 23. The Pi Zero is wired on 19/20 (UART1, 115200) plus GND. Screen, LED, voice, button: the Whisplay HAT.",
        "Full wiring: Hardware/pinout.md · drawing: Hardware/drawings/DRIFTS_Schematic_rev7.pdf",
    ]
    for k, n in enumerate(notes):
        out.append(t(40, y + 22 * k, ("⚠ " if k < 1 else "") + n, 13, "#7a1010" if k < 1 else "#444"))
    out.append("</svg>")
    path = Path(__file__).resolve().parents[1] / "pinout.svg"
    path.write_text("\n".join(out), encoding="utf-8")
    print("wrote", path)


if __name__ == "__main__":
    main()
