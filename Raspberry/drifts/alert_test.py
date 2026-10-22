"""Alert-chain bench test: Pi -> BLE -> ESP32 -> GPIO 10/11 -> motor and buzzer.

The motor and buzzer are wired to the ESP32, not to the Pi. This tool proves
the whole chain from the Pi's side: it connects to DRIFTS-ESP32 exactly as the
real app does, sends DROWSY / NOT_DROWSY / TEST commands over BLE, keeps the
fail-safe heartbeat going, and reads back what the ESP32 reports. You confirm
the physical end (motor felt, buzzer heard, meter reading) and the answers are
saved to a log.

    python -m drifts.alert_test                 # on the Pi, over BLE
    python -m drifts.alert_test --serial COM5   # laptop -> ESP32 over USB, no Pi
    python -m drifts.alert_test --sim           # no hardware: simulated ESP32

Stop the service first on the Pi (sudo systemctl stop drifts): the Pi can
hold only one BLE link to the ESP32.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

from drifts.config import BleConfig, load_config

# Must match config.h on the ESP32.
MOTOR_PIN = 10
BUZZER_PIN = 11       # piezo leg A
PIEZO_B_PIN = 18      # piezo leg B, same signal inverted (firmware v13+)
MAX_TEST_S = 20.0                # MAX_ALERT_MS; the ESP32 clamps to this too
PHONE_STATUS_UUID = "DF720002-B5A3-F393-E0A9-E50E24DCCA9E"

log = logging.getLogger("drifts.alert_test")


# --------------------------------------------------------------------------- #
# Command building -- pure, unit-tested
# --------------------------------------------------------------------------- #
def parse_seconds(text: str, default: float) -> float:
    """'' -> default. Raises ValueError outside 0..MAX_TEST_S."""
    text = text.strip()
    if not text:
        return default
    value = float(text)
    if not 0 <= value <= MAX_TEST_S:
        raise ValueError(f"must be between 0 and {MAX_TEST_S:g} seconds")
    return value


def make_test_command(motor_s: float, buzzer_s: float, buzzer_dc: bool = False) -> str:
    """Build the TEST:<motor_ms>:<buzzer_ms>[:DC] string the firmware parses."""
    for name, v in (("motor", motor_s), ("buzzer", buzzer_s)):
        if not 0 <= v <= MAX_TEST_S:
            raise ValueError(f"{name} duration must be 0..{MAX_TEST_S:g} s, got {v}")
    cmd = f"TEST:{round(motor_s * 1000)}:{round(buzzer_s * 1000)}"
    return cmd + ":DC" if buzzer_dc else cmd


# --------------------------------------------------------------------------- #
# Hubs: the real ESP32 over BLE, or a simulated one
# --------------------------------------------------------------------------- #
class SimHub:
    """Models the ESP32's alert and pin-test behaviour, no radio involved.

    Prints what each GPIO would be doing so the menu can be rehearsed on a
    laptop. Timings mirror config.h.
    """

    ALERT_MS = 20000

    def __init__(self, clock: Callable[[], float] = time.monotonic, out=print):
        self.clock = clock
        self.out = out
        self.alert_until = 0.0
        self.motor_until = 0.0
        self.buzzer_until = 0.0
        self.buzzer_dc = False

    async def connect(self) -> None:
        self.out("[SIM] Simulated ESP32 ready. No hardware is being driven.")

    async def send(self, msg: str) -> None:
        now = self.clock()
        if msg == "PING":
            return
        if msg == "DROWSY":
            self.motor_until = self.buzzer_until = 0.0
            self.alert_until = now + self.ALERT_MS / 1000
            self.out(f"[SIM ESP32] ALERTING: GPIO {MOTOR_PIN} pulses 500/300 ms, "
                     f"GPIO {BUZZER_PIN}/{PIEZO_B_PIN} 5 kHz 300/300 ms, max 20 s")
        elif msg == "NOT_DROWSY":
            self.alert_until = self.motor_until = self.buzzer_until = 0.0
            self.out(f"[SIM ESP32] IDLE: GPIO {MOTOR_PIN} LOW, GPIO {BUZZER_PIN} LOW")
        elif msg.startswith("EYE:"):
            eye = int(msg.split(":")[1])
            alert = expected_alert(eye)
            self.out(f"[SIM ESP32] Eye {eye} -> alert score {alert:.0f} -> {expected_leds(alert)}")
        elif msg.startswith("TEST:"):
            parts = msg.split(":")
            motor_ms, buzzer_ms = int(parts[1]), int(parts[2])
            self.buzzer_dc = len(parts) > 3 and parts[3] == "DC"
            self.alert_until = 0.0
            self.motor_until = now + motor_ms / 1000
            self.buzzer_until = now + buzzer_ms / 1000
            self.out(f"[SIM ESP32] TEST: GPIO {MOTOR_PIN} HIGH for {motor_ms} ms, "
                     f"GPIO {BUZZER_PIN}/{PIEZO_B_PIN} {'DC (11 HIGH, 18 LOW)' if self.buzzer_dc else 'tone'} for {buzzer_ms} ms")
        else:
            self.out(f"[SIM ESP32] Ignored unknown message: {msg}")

    def status(self) -> Optional[dict]:
        now = self.clock()
        return {
            "alert": now < self.alert_until,
            "test": now < self.motor_until or now < self.buzzer_until,
            "pi": "sim",
        }

    async def close(self) -> None:
        pass


_TEST_LINE = re.compile(r"\[TEST\] Motor GPIO \d+: (?P<motor>\d+) ms.*Buzzer GPIO [\d/]+: (?P<buzzer>\d+) ms")


class SerialHub:
    """The real ESP32 over USB serial, no Pi and no Bluetooth.

    Sends the same commands the Pi would send over BLE (the firmware accepts
    them on both inputs) and follows the ESP32's own log to know whether an
    alert or a pin test is running. ESP32 log lines are echoed with an
    "ESP32|" prefix so the tester sees exactly what the board reports.
    """

    def __init__(self, port_name: str, out=print, port=None):
        self.port_name = port_name
        self.out = out
        self.port = port               # injectable for tests
        self.alert = False
        self.motor_on = False
        self.buzzer_on = False
        self.seen_lines = 0
        self._reader: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        if self.port is None:
            try:
                import serial
            except ImportError as exc:
                raise ConnectionError("pyserial is missing: pip install pyserial") from exc
            port = serial.Serial()
            port.port, port.baudrate, port.timeout = self.port_name, 115200, 0.1
            port.dtr = False             # DTR/RTS drive reset/BOOT on the CH343 board
            port.rts = False
            try:
                port.open()
            except serial.SerialException as exc:
                raise ConnectionError(f"cannot open {self.port_name}: {exc}. Close miniterm first.") from exc
            self.port = port
        self._reader = asyncio.create_task(self._read_loop())
        self.out(f"Connected to the ESP32 on {self.port_name} (USB serial). No Pi involved.")
        await self.send("STATUS")

    async def _read_loop(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            raw = await loop.run_in_executor(None, self.port.readline)
            if raw:
                self.on_line(raw.decode("utf-8", "replace").strip())
            else:
                await asyncio.sleep(0.05)

    def on_line(self, line: str) -> None:
        """Track output state from the firmware's log lines."""
        if not line:
            return
        self.seen_lines += 1
        if line.startswith(("[MAIN]", "[TEST]", "[CMD]", "[STATUS]", "[SCORE]", "[LED]")):
            self.out(f"    ESP32| {line}")
        if "-> ALERTING" in line:
            self.alert = True
        elif "-> IDLE" in line:
            self.alert = False
        elif m := _TEST_LINE.search(line):
            # "[TEST] Motor GPIO 10: 2000 ms at duty 255/255 | Buzzer GPIO 11/18: 0 ms 5 kHz differential tone"
            # (firmware before v13 printed "Buzzer GPIO 11:"; both match)
            self.motor_on = int(m["motor"]) > 0
            self.buzzer_on = int(m["buzzer"]) > 0
        elif line.startswith("[TEST] Motor off"):
            self.motor_on = False
        elif line.startswith("[TEST] Buzzer off"):
            self.buzzer_on = False

    async def send(self, msg: str) -> None:
        if msg == "PING":                # heartbeat only matters on the BLE link
            return
        self.port.write((msg + "\n").encode())

    def status(self) -> Optional[dict]:
        if self.seen_lines == 0:
            return None
        return {"alert": self.alert, "test": self.motor_on or self.buzzer_on, "pi": "none (USB)"}

    async def close(self) -> None:
        try:
            await self.send("STOP")
            await asyncio.sleep(0.3)
        except Exception:
            pass
        if self._reader:
            self._reader.cancel()
        try:
            self.port.close()
        except Exception:
            pass


class BleHub:
    """The real ESP32, found by name, driven through the Pi result characteristic."""

    def __init__(self, cfg: BleConfig):
        self.cfg = cfg
        self.client = None
        self._status: Optional[dict] = None
        self._status_at = 0.0
        self._hb: Optional[asyncio.Task] = None

    async def connect(self) -> None:
        from bleak import BleakClient, BleakScanner

        target = self.cfg.address or self.cfg.device_name
        print(f"Scanning for {target} ...")
        if self.cfg.address:
            device = await BleakScanner.find_device_by_address(self.cfg.address, timeout=10.0)
        else:
            device = await BleakScanner.find_device_by_name(self.cfg.device_name, timeout=10.0)
        if device is None:
            raise ConnectionError(
                f"{target} not found. Is the ESP32 powered, flashed with the DF71 UUID "
                "firmware, and within a few metres? It shows in nRF Connect when it is advertising."
            )
        print(f"Connecting to {device.name} ({device.address}) ...")
        self.client = BleakClient(device)
        await self.client.connect()
        try:
            await self.client.start_notify(PHONE_STATUS_UUID, self._on_status)
        except Exception as exc:          # status is a nice-to-have, not required
            log.debug("status notify unavailable: %s", exc)
        self._hb = asyncio.create_task(self._heartbeat())
        print("Connected. Heartbeat running (PING every "
              f"{self.cfg.heartbeat_s:g} s) so the ESP32 fail-safe stays satisfied.")

    def _on_status(self, _sender, data: bytearray) -> None:
        try:
            self._status = json.loads(bytes(data).decode())
            self._status_at = time.monotonic()
        except (ValueError, UnicodeDecodeError):
            pass

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.cfg.heartbeat_s)
            try:
                await self.send("PING")
            except Exception as exc:
                log.debug("PING failed: %s", exc)

    async def send(self, msg: str) -> None:
        if not self.client or not self.client.is_connected:
            raise ConnectionError("not connected to the ESP32")
        await self.client.write_gatt_char(self.cfg.result_char_uuid, msg.encode(), response=False)

    def status(self) -> Optional[dict]:
        # Telemetry arrives every PHONE_UPDATE_MS; treat anything older than 5 s as stale.
        if self._status is None or time.monotonic() - self._status_at > 5:
            return None
        return self._status

    async def close(self) -> None:
        if self._hb:
            self._hb.cancel()
        if self.client and self.client.is_connected:
            try:
                await self.send("NOT_DROWSY")
            except Exception:
                pass
            await self.client.disconnect()


# --------------------------------------------------------------------------- #
# Interactive session
# --------------------------------------------------------------------------- #
def _box(title: str, rows: list[str], width: int = 64) -> str:
    inner = width - 2
    top = "  ┌" + f" {title} ".center(inner, "─") + "┐"
    body = ["  │" + ("  " + r).ljust(inner) + "│" for r in rows]
    return "\n".join([""] + [top] + body + ["  └" + "─" * inner + "┘"])


MENU = _box("DRIFTS alert-chain test", [
    "D  DROWSY      full alert: motor + buzzer pulse (<=20 s)",
    "N  NOT_DROWSY  stop everything",
    "T  TEST        hold motor / buzzer on for set durations",
    "E  EYE         send an eye score 0-100 (LED bands)",
    "S  STATUS      what the ESP32 reports right now",
    "Q  QUIT        stop outputs, save results, disconnect",
]) + f"""
  Motor = GPIO {MOTOR_PIN}   Buzzer = GPIO {BUZZER_PIN} + {PIEZO_B_PIN}   LEDs: green 2, yellow 3, red 4"""


# Mirrors alertness.h: alert = 0.7 x eye + 0.3 x driving. Bands: OK >= 70,
# CAUTION 50-69 (yellow solid), LOW < 50 (red fast blink).
def expected_alert(eye: int, driving: float = 100.0) -> float:
    return 0.7 * eye + 0.3 * driving


def expected_leds(alert: float) -> str:
    if alert < 50:
        return "RED fast blink (drowsy), yellow off"
    if alert < 70:
        return "YELLOW solid (caution), red off"
    return "red and yellow off (OK)"


class Session:
    def __init__(self, hub, ask: Callable[[str], "asyncio.Future[str]"], log_dir: Path):
        self.hub = hub
        self.ask = ask
        self.results: list[tuple[str, str, str]] = []   # (time, check, answer)
        self.log_dir = log_dir

    async def confirm(self, question: str) -> None:
        answer = (await self.ask(f"  ? {question} [y/n/skip]: ")).strip().lower() or "skip"
        answer = {"y": "PASS", "yes": "PASS", "n": "FAIL", "no": "FAIL"}.get(answer, "SKIP")
        self.results.append((dt.datetime.now().strftime("%H:%M:%S"), question, answer))
        if answer == "FAIL":
            print("    -> See Hardware/wiring-check.md for what to check on this output.")

    async def show_status(self) -> None:
        st = self.hub.status()
        if st is None:
            print("  ESP32 status: no telemetry yet (it arrives about once a second).")
        else:
            print(f"  ESP32 status: alert={st.get('alert')} test={st.get('test')} pi={st.get('pi')}")

    async def do_drowsy(self) -> None:
        await self.hub.send("DROWSY")
        print("  Sent DROWSY. Expect: motor pulsing 0.5 s on / 0.3 s off, buzzer beeping")
        print("  5 kHz 0.3 s on / 0.3 s off. It runs until N, Q, or 20 s.")
        await asyncio.sleep(1.5)
        await self.show_status()
        await self.confirm("DROWSY: motor pulsing?")
        await self.confirm("DROWSY: buzzer beeping?")

    async def do_not_drowsy(self) -> None:
        await self.hub.send("NOT_DROWSY")
        print("  Sent NOT_DROWSY. Motor and buzzer should stop at once.")
        await asyncio.sleep(1.0)
        await self.show_status()
        await self.confirm("NOT_DROWSY: both outputs stopped?")

    async def do_eye(self) -> None:
        try:
            text = (await self.ask("  Eye score 0-100 (stands in for the Pi camera) [80]: ")).strip() or "80"
            eye = int(text)
            if not 0 <= eye <= 100:
                raise ValueError
        except ValueError:
            print("  Not sent: enter a whole number from 0 to 100")
            return
        await self.hub.send(f"EYE:{eye}")
        alert = expected_alert(eye)
        print(f"  Sent EYE:{eye}. With calm driving the alert score becomes about {alert:.0f}.")
        print(f"  Expect: {expected_leds(alert)}.")
        print("  (Raising the score back needs 5 points of margin: 75 for OK, 55 to leave LOW.)")
        await asyncio.sleep(1.5)
        await self.confirm(f"EYE {eye}: LEDs show {expected_leds(alert)}?")

    async def do_test(self) -> None:
        try:
            motor_s = parse_seconds(await self.ask(f"  Motor ON seconds (0-{MAX_TEST_S:g}) [3]: "), 3.0)
            buzzer_s = parse_seconds(await self.ask(f"  Buzzer ON seconds (0-{MAX_TEST_S:g}) [3]: "), 3.0)
        except ValueError as exc:
            print(f"  Not sent: {exc}")
            return
        mode = (await self.ask("  Buzzer mode: [T]one (audible) or [D]C (steady 3.3 V, silent, for a meter) [T]: ")).strip().lower()
        dc = mode.startswith("d")
        cmd = make_test_command(motor_s, buzzer_s, dc)
        await self.hub.send(cmd)
        print(f"  Sent {cmd}")
        print(f"  GPIO {MOTOR_PIN} (motor) HIGH for {motor_s:g} s; "
              f"GPIO {BUZZER_PIN}/{PIEZO_B_PIN} (buzzer) {'DC: 11 HIGH, 18 LOW' if dc else 'tone'} for {buzzer_s:g} s.")
        if dc:
            print(f"  Meter, black probe on GND: GPIO {BUZZER_PIN} should read about 3.3 V while on.")
        longest = max(motor_s, buzzer_s)
        await asyncio.sleep(min(1.0, longest))
        await self.show_status()
        if longest > 1.0:
            print(f"  Running ... ({longest:g} s total)")
            await asyncio.sleep(longest - 1.0 + 0.3)
        if motor_s > 0:
            await self.confirm(f"TEST: motor ran for about {motor_s:g} s, then stopped?")
        if buzzer_s > 0:
            what = "meter read ~3.3 V on GPIO 11 (and ~0 V on 18)" if dc else "buzzer sounded"
            await self.confirm(f"TEST: {what} for about {buzzer_s:g} s, then stopped?")

    def save(self) -> Optional[Path]:
        if not self.results:
            return None
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"alert_test_{dt.datetime.now():%Y%m%d_%H%M%S}.log"
        lines = [f"DRIFTS alert-chain test {dt.datetime.now():%Y-%m-%d %H:%M}",
                 f"hub: {type(self.hub).__name__}", ""]
        lines += [f"{t}  {a:4}  {q}" for t, q, a in self.results]
        passed = sum(a == "PASS" for _, _, a in self.results)
        failed = sum(a == "FAIL" for _, _, a in self.results)
        lines += ["", f"{passed} pass, {failed} fail, {len(self.results) - passed - failed} skipped"]
        path.write_text("\n".join(lines) + "\n")
        return path

    async def run(self) -> None:
        await self.hub.connect()
        try:
            while True:
                print(MENU)
                choice = (await self.ask("Select D / N / T / E / S / Q: ")).strip().upper()
                if choice == "D":
                    await self.do_drowsy()
                elif choice == "N":
                    await self.do_not_drowsy()
                elif choice == "T":
                    await self.do_test()
                elif choice == "E":
                    await self.do_eye()
                elif choice == "S":
                    await self.show_status()
                elif choice in ("Q", "QUIT", "EXIT"):
                    break
                else:
                    print("  Unknown choice.")
        finally:
            await self.hub.close()
            path = self.save()
            print("\nOutputs stopped, disconnected.")
            if path:
                print(f"Results saved to {path}")
                print(path.read_text())


def service_running() -> bool:
    try:
        r = subprocess.run(["systemctl", "is-active", "drifts"], capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == "active"
    except (OSError, subprocess.SubprocessError):
        return False


async def _ask(prompt: str) -> str:
    return await asyncio.get_running_loop().run_in_executor(None, input, prompt)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m drifts.alert_test", description=__doc__.split("\n")[0])
    ap.add_argument("--sim", action="store_true", help="simulated ESP32, no hardware needed")
    ap.add_argument("--serial", metavar="PORT",
                    help="drive the ESP32 directly over USB (e.g. COM5), no Pi or Bluetooth")
    ap.add_argument("--config", "-c", metavar="PATH", help="config.toml to read BLE settings from")
    ap.add_argument("--log-dir", default=str(Path.home() / "drifts_test_logs"), help="where to save results")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARNING)

    if args.sim:
        hub = SimHub()
    elif args.serial:
        hub = SerialHub(args.serial)
    else:
        if service_running():
            print("The drifts service is running and already holds the BLE link to the ESP32.\n"
                  "Stop it first:   sudo systemctl stop drifts\n"
                  "Restart after:   sudo systemctl start drifts")
            return 2
        hub = BleHub(load_config(args.config).ble)

    try:
        asyncio.run(Session(hub, _ask, Path(args.log_dir)).run())
    except ConnectionError as exc:
        print(f"\nConnection problem: {exc}")
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
