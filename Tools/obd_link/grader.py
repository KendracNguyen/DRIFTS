"""Grade the ESP32 <-> BLE OBD dongle link from the ESP32's serial log.

Pure parsing, no serial port: feed it lines, read the verdict. The line
formats come from Esp32/drifts_esp32/obd_ble.cpp and the [WATCH] line in
drifts_esp32.ino -- change them together.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# (key, label) in the order the link comes up.
STAGES = [
    ("scan", "ESP32 scanning for a dongle"),
    ("found", "Dongle found (name + MAC)"),
    ("connect", "BLE connected (and stayed up)"),
    ("layout", "GATT data channel matched"),
    ("elm", "ELM327 initialised"),
    ("pids", "Vehicle PIDs reported"),
    ("polling", "Live data flowing (hz > 0)"),
]

PASS, FAIL, PENDING, EXPECTED, SKIP = "PASS", "FAIL", "PENDING", "EXPECTED", "SKIP"

HINTS = {
    "scan": "No [OBD] lines at all. Wrong COM port, firmware older than this tool, or the board is not running.",
    "found": "Dongle not seen. Is it powered (in the car, ignition on, or on 12 V)? Close Torque / Car Scanner / "
             "nRF Connect -- a BLE dongle serves one central at a time. If it advertises an unusual name, add it "
             "to OBD_NAME_HINTS or set OBD_MAC_ADDRESS in config.h.",
    "connect": "Found, but the link would not come up or dropped straight after connecting. HCI 0x08 (reason "
               "520) means radio contact was lost: move the ESP32 within ~2 m of the dongle, make sure the dongle "
               "is properly powered (in the car with ignition on), close every phone OBD app, and power-cycle the "
               "dongle. Needs firmware v5 or later, which waits longer before giving up on the link.",
    "layout": "Connected but the data channel is unknown. Copy the '[OBD]   <uuid>: n notify, n write' lines from "
              "the log into LAYOUTS[] in obd_ble.cpp (see Docs/dongle-bringup.md Phase A).",
    "elm": "The dongle answered BLE but ELM327 init failed. In a car: ignition ON, then try ELM_PROTOCOL '0' "
           "(auto-detect) once. On a desk this is expected -- use --mode desk.",
    "pids": "ELM ready but no supported-PID line. Re-run; if it repeats, capture the log for the team.",
    "polling": "Connected and initialised but no data (hz stayed 0). Engine/ignition on? Check the nodata/err "
               "counters -- NO DATA on every PID means wrong protocol.",
}

_RE = {
    "scan": re.compile(r"\[OBD\] Scanning for a BLE OBD dongle"),
    "found": re.compile(r"\[OBD\] Candidate: (?P<name>.*) \((?P<mac>[0-9A-Fa-f:]{17})\)"),
    "connect": re.compile(r"\[OBD\] Connected, MTU (?P<mtu>\d+)"),
    "connect_fail": re.compile(r"\[OBD\] connect\(\) failed"),
    "layout": re.compile(r"\[OBD\] Matched layout: (?P<layout>.+)"),
    "layout_probed": re.compile(r"\[OBD\] Using probed layout"),
    "layout_fail": re.compile(r"\[OBD\] Could not identify the dongle's data channel"),
    "probe": re.compile(r"\[OBD\]\s+(?P<uuid>[0-9a-fA-Fx-]+): (?P<n>\d+) notify, (?P<w>\d+) write"),
    "elm_init": re.compile(r"\[OBD\] Initialising ELM327 \(protocol '(?P<proto>.)'\)"),
    "elm_ok": re.compile(r"\[OBD\] ELM327 ready"),
    "elm_fail": re.compile(r"\[OBD\] ELM327 init failed"),
    "pids": re.compile(r"\[OBD\] supported: RPM=(?P<rpm>\d) SPEED=(?P<spd>\d) THROTTLE=(?P<thr>\d) LOAD=(?P<load>\d)"),
    "link_down": re.compile(r"\[OBD\] Link down: (?P<why>.+)"),
    "disconnect": re.compile(r"\[OBD\] Dongle disconnected \(reason (?P<reason>\d+)"),
    "lost_after_connect": re.compile(r"\[OBD\] Lost the dongle right after connecting"),
    "watch": re.compile(
        r"\[WATCH\] obd=(?P<obd>\w+) layout=(?P<layout>.*?) hz=(?P<hz>[\d.]+) rpm=(?P<rpm>-?[\d.]+) "
        r"spd=(?P<spd>-?[\d.]+) thr=(?P<thr>-?[\d.]+) load=(?P<load>-?[\d.]+) "
        r"nodata=(?P<nodata>\d+) err=(?P<err>\d+) ovf=(?P<ovf>\d+)"),
}


@dataclass
class Facts:
    name: str = ""
    mac: str = ""
    mtu: Optional[int] = None
    layout: str = ""
    protocol: str = ""
    pids: dict = field(default_factory=dict)
    hz: list = field(default_factory=list)
    last_watch: dict = field(default_factory=dict)
    link_downs: list = field(default_factory=list)
    probed_services: list = field(default_factory=list)
    disconnects: list = field(default_factory=list)


class LinkGrader:
    """mode 'car': everything through live data must pass.
    mode 'desk': dongle powered but not in a vehicle -- ELM init is expected to
    fail once the BLE layer is proven, so the test ends at the layout stage.
    """

    def __init__(self, mode: str = "car", poll_seconds: int = 10):
        if mode not in ("car", "desk"):
            raise ValueError("mode must be 'car' or 'desk'")
        self.mode = mode
        self.poll_seconds = poll_seconds
        self.status = {k: PENDING for k, _ in STAGES}
        self.facts = Facts()
        self._good_polls = 0
        self._failed_at: Optional[str] = None
        self._dropped_before_layout = False
        if mode == "desk":
            self.status["pids"] = self.status["polling"] = SKIP

    # -- feeding ------------------------------------------------------------------
    def feed(self, line: str) -> Optional[str]:
        """Consume one log line. Returns the stage key it advanced, if any."""
        line = line.strip()
        f = self.facts
        if _RE["scan"].search(line):
            return self._pass("scan")
        if m := _RE["found"].search(line):
            f.name, f.mac = m["name"], m["mac"]
            return self._pass("found", "scan")
        if m := _RE["connect"].search(line):
            f.mtu = int(m["mtu"])
            self._dropped_before_layout = False
            return self._pass("connect", "scan", "found")
        if _RE["connect_fail"].search(line):
            self._fail("connect")
            return "connect"
        if m := _RE["layout"].search(line):
            f.layout = m["layout"].strip()
            return self._pass("layout", "scan", "found", "connect")
        if _RE["layout_probed"].search(line):
            f.layout = "probed"
            return self._pass("layout", "scan", "found", "connect")
        if m := _RE["probe"].search(line):
            f.probed_services.append(f"{m['uuid']}: {m['n']} notify, {m['w']} write")
            return None
        if _RE["layout_fail"].search(line):
            # Firmware before v5 blamed the layout when the link had already
            # dropped; that is a connection failure, not an unknown dongle.
            if self._dropped_before_layout:
                self._fail("connect")
                return "connect"
            self._fail("layout")
            return "layout"
        if m := _RE["disconnect"].search(line):
            reason = int(m["reason"])
            f.disconnects.append(f"{reason} (HCI 0x{reason & 0xFF:02X})")
            if self.status["connect"] == PASS and self.status["layout"] != PASS:
                self._dropped_before_layout = True
                self.status["connect"] = FAIL
                self._failed_at = self._failed_at or "connect"
                return "connect"
            return None
        if _RE["lost_after_connect"].search(line):
            self._dropped_before_layout = True
            self.status["connect"] = FAIL
            return "connect"
        if m := _RE["elm_init"].search(line):
            f.protocol = m["proto"]
            return None
        if _RE["elm_ok"].search(line):
            return self._pass("elm", "scan", "found", "connect", "layout")
        if _RE["elm_fail"].search(line):
            if self.mode == "desk":
                self.status["elm"] = EXPECTED
            else:
                self._fail("elm")
            return "elm"
        if m := _RE["pids"].search(line):
            f.pids = {k: m[k] == "1" for k in ("rpm", "spd", "thr", "load")}
            return self._pass("pids", "scan", "found", "connect", "layout", "elm")
        if m := _RE["link_down"].search(line):
            f.link_downs.append(m["why"].strip())
            return None
        if m := _RE["watch"].search(line):
            return self._watch(m)
        return None

    def _watch(self, m) -> Optional[str]:
        f = self.facts
        f.last_watch = {k: m[k] for k in ("obd", "layout", "hz", "rpm", "spd", "thr", "load", "nodata", "err", "ovf")}
        if self.mode == "desk" or m["obd"] != "polling":
            return None
        hz = float(m["hz"])
        # Joined mid-session (no restart): earlier stages happened before we listened.
        for k in ("scan", "found", "connect", "layout", "elm", "pids"):
            if self.status[k] == PENDING:
                self.status[k] = SKIP
        if not f.layout and m["layout"] not in ("", "none"):
            f.layout = m["layout"]
        if hz > 0:
            f.hz.append(hz)
            self._good_polls += 1
            if self._good_polls >= self.poll_seconds and self.status["polling"] == PENDING:
                return self._pass("polling")
        return None

    def _pass(self, key: str, *implied: str) -> str:
        for k in implied:                     # a later stage proves the earlier ones
            if self.status[k] == PENDING:
                self.status[k] = PASS
        if self.status[key] in (PENDING, FAIL):
            self.status[key] = PASS
        return key

    def _fail(self, key: str) -> None:
        if self.status[key] != PASS:
            self.status[key] = FAIL
            self._failed_at = self._failed_at or key

    # -- verdict ------------------------------------------------------------------------
    def done(self) -> bool:
        """True once the outcome cannot change by waiting longer."""
        if self.mode == "desk":
            return self.status["layout"] == PASS or self.status["layout"] == FAIL
        return self.status["polling"] == PASS

    def verdict(self) -> str:
        if self.mode == "desk":
            return PASS if self.status["layout"] == PASS else FAIL
        return PASS if self.status["polling"] == PASS else FAIL

    def first_problem(self) -> Optional[str]:
        """The earliest stage that did not pass -- where to look first."""
        for k, _ in STAGES:
            if self.status[k] in (FAIL, PENDING):
                return k
        return None

    def warnings(self) -> list[str]:
        w = []
        ovf = int(self.facts.last_watch.get("ovf", 0) or 0)
        if ovf:
            w.append(f"ovf={ovf}: frames dropped in the BLE stream. Something is blocking loop(); "
                     "statistics from this run are suspect.")
        if self.facts.hz and min(self.facts.hz) < 2:
            w.append(f"Rate dipped to {min(self.facts.hz):.1f} Hz (expected 2-11). Check for a phone link "
                     "competing for airtime or a weak signal.")
        p = self.facts.pids
        if p and not all(p.values()):
            missing = [k.upper() for k, v in p.items() if not v]
            w.append(f"Vehicle does not support: {', '.join(missing)}. Not a link fault; the trigger uses the rest.")
        if self.facts.disconnects:
            w.append("Dongle disconnected: reason " + ", ".join(self.facts.disconnects[-3:]))
        if self.facts.link_downs:
            w.append("Link dropped during the run: " + "; ".join(self.facts.link_downs[-3:]))
        return w

    def report(self) -> str:
        f = self.facts
        lines = [f"OBD BLE link test -- mode: {self.mode}", ""]
        for k, label in STAGES:
            lines.append(f"  {self.status[k]:8}  {label}")
        lines.append("")
        lines.append(f"  RESULT: {self.verdict()}")
        lines += ["", "Measured:"]
        lines.append(f"  dongle     {f.name or '-'}  {f.mac or ''}")
        lines.append(f"  MTU        {f.mtu if f.mtu is not None else '-'}")
        lines.append(f"  layout     {f.layout or '-'}")
        lines.append(f"  protocol   {f.protocol or '-'}")
        if f.pids:
            lines.append("  PIDs       " + "  ".join(f"{k.upper()}={'yes' if v else 'no'}" for k, v in f.pids.items()))
        if f.hz:
            lines.append(f"  rate       avg {sum(f.hz) / len(f.hz):.1f} Hz, min {min(f.hz):.1f}, max {max(f.hz):.1f} "
                         f"over {len(f.hz)} s")
        if f.last_watch:
            w = f.last_watch
            lines.append(f"  last       rpm {w['rpm']}  speed {w['spd']} km/h  throttle {w['thr']}%  load {w['load']}%")
            lines.append(f"  counters   nodata {w['nodata']}  err {w['err']}  ovf {w['ovf']}")
        if f.probed_services:
            lines += ["", "Services probed (copy into LAYOUTS[] if no layout matched):"]
            lines += [f"  {s}" for s in f.probed_services]
        warns = self.warnings()
        if warns:
            lines += ["", "Warnings:"] + [f"  ! {x}" for x in warns]
        problem = self.first_problem() if self.verdict() == FAIL else None
        if problem:
            lines += ["", f"Look first at: {dict(STAGES)[problem]}", f"  {HINTS[problem]}"]
        return "\n".join(lines)
