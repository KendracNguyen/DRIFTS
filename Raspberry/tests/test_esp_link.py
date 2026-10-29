"""Parsing of ESP32 serial log lines and phone telemetry JSON."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.esp_link import SimLink, parse_phone_json, parse_serial_line

# Real lines from firmware v14 (Oct 29 2026 bench session)
STATUS = ("[STATUS] obd=scanning layout=none 0.0 Hz | rpm 0 (sd 0, n 0) spd 0 (sd 0.0, n 0) "
          "thr 0 (sd 0.0, n 0) | brakes 0 accel=0 steer 0 yaw +0.0 | gate=BENCH | pi=0 phone=0 | "
          "nodata 0 err 0 ovf 0")
SCORE = ("[SCORE] alert 100 (OK) = eye 90 (est.) x0.7 + driving 100 x0.3 | PARTIAL: no eye data, "
         "driving only | LEDs G=slow Y=slow R=off | system issue: bench mode | LCD absent")


def test_firmware_version():
    assert parse_serial_line("[STATUS] firmware v14") == {"fw": "v14"}


def test_status_line():
    d = parse_serial_line(STATUS)
    assert d == {"obd": "scanning", "hz": 0.0, "rpm": 0.0, "spd": 0.0, "thr": 0.0}


def test_status_line_polling():
    line = ("[STATUS] obd=polling layout=FFF0 4.6 Hz | rpm 812 (sd 21, n 40) spd 37 (sd 1.2, n 40) "
            "thr 15 (sd 0.4, n 40) | brakes 0 accel=1 steer 0 yaw +0.3 | gate=open | pi=1 phone=0")
    d = parse_serial_line(line)
    assert d["obd"] == "polling" and d["hz"] == 4.6
    assert d["rpm"] == 812 and d["spd"] == 37 and d["thr"] == 15


def test_score_line():
    d = parse_serial_line(SCORE)
    assert d["score"] == 100 and d["lvl"] == "OK" and d["eye"] == 90
    assert d["part"] is True
    assert d["lcd1"] is None


def test_lcd_line():
    d = parse_serial_line("[LCD] | INTERVENTION 1 | |Signs of Fatigue|")
    assert d == {"lcd1": " INTERVENTION 1 ", "lcd2": "Signs of Fatigue"}


def test_alert_transitions():
    assert parse_serial_line("[MAIN] -> ALERTING") == {"alert": True}
    assert parse_serial_line("[MAIN] -> IDLE (manual STOP)") == {"alert": False}


def test_noise_lines():
    assert parse_serial_line("[OBD] Scanning for a BLE OBD dongle...") == {}
    assert parse_serial_line("") == {}


def test_phone_json():
    js = ('{"rpm":812,"spd":37.0,"thr":15.2,"g":0.01,"yaw":0.3,"steer":0,"obd":"polling","hz":4.6,'
          '"gate":"open","drv":"OK","pi":"idle","alert":false,"test":false,"score":63,"lvl":"CAUTION",'
          '"part":false}')
    d = parse_phone_json(js)
    assert d == {"rpm": 812.0, "spd": 37.0, "thr": 15.2, "hz": 4.6, "score": 63.0,
                 "obd": "polling", "lvl": "CAUTION", "part": False, "alert": False}


def test_phone_json_garbage():
    assert parse_phone_json("{not json") == {}
    assert parse_phone_json("") == {}


def test_sim_link_follows_eye_score():
    link = SimLink()
    link.start()
    link.send_eye(20)
    import time
    time.sleep(1.3)
    snap = link.snapshot()
    link.stop()
    assert snap["link"] == "simulated"
    assert snap["lvl"] == "LOW"           # 0.7*20 + 30 = 44
    assert snap["lcd1"] == " INTERVENTION 2 "
