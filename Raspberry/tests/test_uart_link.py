"""The wired Pi <-> ESP32 link (firmware v21): drifts/uart_client.py and
esp_link.UartLink, with a fake serial port."""

import asyncio
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from drifts.config import Config, LinkConfig, load_config
from drifts.uart_client import LineParser, UartClient, make_client

ROOT = Path(__file__).resolve().parents[2]


class FakeSerial:
    """Reads what the test queues; records what the client writes."""

    def __init__(self):
        self.rx = []
        self.tx = []
        self.lock = threading.Lock()

    def push(self, data: bytes):
        with self.lock:
            self.rx.append(data)

    def read(self, n):
        with self.lock:
            if self.rx:
                return self.rx.pop(0)
        time.sleep(0.01)
        return b""

    def write(self, data):
        self.tx.append(data.decode())

    def close(self):
        pass


def test_line_parser_keeps_partial_lines():
    p = LineParser()
    assert p.feed(b"WA") == []
    assert p.feed(b"KE\r\nSTATUS {\"score\":71}\n\nID") == ["WAKE", 'STATUS {"score":71}']
    assert p.feed(b"LE\n") == ["IDLE"]
    assert LineParser(max_len=8).feed(b"x" * 20) == []                  # junk without a line end is dropped


def test_messages_from_the_esp32():
    c = UartClient(LinkConfig())
    c.handle_line("HELLO v21", now=1.0)
    assert c.connected and c.fw == "v21"
    c.handle_line('STATUS {"score":63,"lvl":"CAUTION","alert":false,"obd":"polling","dscore":71}', now=2.0)
    assert c.status["score"] == 63 and c.status["drv_score"] == 71 and c.status_t == 2.0
    assert not c.wake_event.is_set()
    c.handle_line("WAKE", now=3.0)
    assert c.wake_event.is_set()


def test_round_trip_over_a_fake_wire():
    fake = FakeSerial()
    c = UartClient(LinkConfig(link_s=0.5), serial_factory=lambda: fake)

    async def scenario():
        task = asyncio.create_task(c.run())
        hb = asyncio.create_task(c.heartbeat())
        c.heartbeat_s = 0.05
        await asyncio.sleep(0.1)
        fake.push(b"HELLO v21\nSTATUS {\"score\":88,\"lvl\":\"OK\",\"obd\":\"polling\"}\nWAKE\n")
        await asyncio.wait_for(c.wake_event.wait(), 2.0)
        assert await c.send_result("DROWSY")
        await asyncio.sleep(0.8)                                         # nothing more: silent past link_s
        connected_after_silence = c.connected
        c.stop()
        for t in (task, hb):
            t.cancel()
        return connected_after_silence

    lost = asyncio.run(scenario())
    assert c.status["score"] == 88 and c.fw == "v21"
    assert "DROWSY\n" in fake.tx and "PING\n" in fake.tx                # result and the fail-safe heartbeat
    assert lost is False                                                 # silence = link lost


def test_link_choice_from_config():
    cfg = load_config(ROOT / "Raspberry" / "config.toml")
    assert cfg.link.kind == "uart" and cfg.link.uart_port == "/dev/serial0" and cfg.link.uart_baud == 115200
    assert isinstance(make_client(cfg), UartClient)
    cfg.link.kind = "ble"
    from drifts.ble_client import BleClient
    assert isinstance(make_client(cfg), BleClient)
    cfg.link.kind = "wifi"
    with pytest.raises(ValueError):
        make_client(cfg)
    assert Config().link.kind == "uart"


def test_esp_link_uart_lines():
    from drifts.esp_link import UartLink, make_link
    link = UartLink()
    link.handle_line('STATUS {"score":55,"lvl":"CAUTION","obd":"polling","rpm":900}')
    link.handle_line("HELLO v21")
    link.handle_line("[MAIN] something")
    snap = link.snapshot()
    assert snap["score"] == 55 and snap["rpm"] == 900 and snap["fw"] == "v21" and snap["updated"]
    assert "[MAIN] something" in link.log_lines
    assert make_link("uart", port="COM5").port_name == "/dev/serial0"   # the laptop default isn't a Pi UART


def test_firmware_and_pi_agree_on_the_wire():
    cfg_h = (ROOT / "Esp32/drifts_esp32/config.h").read_text()
    assert "PI_UART_TX_PIN = 19" in cfg_h and "PI_UART_RX_PIN = 20" in cfg_h
    assert "PI_UART_BAUD = 115200" in cfg_h and LinkConfig().uart_baud == 115200
    src = (ROOT / "Esp32/drifts_esp32/pi_uart.cpp").read_text()
    for word in ('"WAKE"', '"IDLE"', '"STATUS "', '"PING"', '"HELLO "'):
        assert word in src, word
