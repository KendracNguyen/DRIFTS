"""The phone's camera preview overlay and the Settings test buttons (drifts/live.py)."""

import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from drifts.live import TESTS, make_handler
from test_privacy import make


def test_overlay_draws_without_text_and_crops_to_the_card(tmp_path):
    p = make(tmp_path)
    img = np.zeros((688, 464, 3), np.uint8)                 # a portrait frame
    p._draw_face(img, p.camera.landmarks, mirrored=True)
    assert img.any()                                         # something was drawn
    p.pose.update(yaw=None)
    out = p._preview_crop(img)
    h, w = out.shape[:2]
    assert w == 464 and abs(w / h - 1.6) < 0.01              # the card's 16:10
    p._draw_face(np.zeros((480, 640, 3), np.uint8), None, mirrored=False)   # no face: no error


def test_settings_test_buttons_send_the_pin_test(tmp_path):
    p = make(tmp_path)
    sent = []
    p.link.send_command = sent.append
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(p))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        for what in ("buzzer", "vibration"):
            assert urllib.request.urlopen(urllib.request.Request(f"{base}/test?what={what}", method="POST")).status == 200
        try:
            urllib.request.urlopen(urllib.request.Request(f"{base}/test?what=horn", method="POST"))
            assert False, "unknown test accepted"
        except urllib.error.HTTPError as e:
            assert e.code == 400
    finally:
        srv.shutdown()
    assert sent == ["TEST:0:2000", "TEST:2000:0"] == [TESTS["buzzer"], TESTS["vibration"]]
    assert [m for _, m in p.sent_log] == sent               # shown under "What leaves the camera"


def test_home_has_status_beside_score_and_ai_is_a_chat_button():
    html = (Path(__file__).resolve().parents[1] / "drifts" / "dashboard.html").read_text()
    assert "Current Status" not in html and 'class="mini"' in html
    assert 'data-t="ai"' not in html and 'id="fab"' in html
    assert "testEsp('buzzer'" in html and "testEsp('vibration'" in html
    assert "It measures your face for raw data. No video or pictures are recorded. See what it sees." in html


def test_app_icon_and_manifest(tmp_path):
    import json
    p = make(tmp_path)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(p))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        for n in (32, 180, 192, 512):
            r = urllib.request.urlopen(f"{base}/static/icons/icon-{n}.png")
            assert r.headers["Content-Type"] == "image/png" and r.read(8) == b"\x89PNG\r\n\x1a\n"
        m = json.loads(urllib.request.urlopen(f"{base}/manifest.webmanifest").read())
        assert m["name"] == "DRIFTS" and {i["sizes"] for i in m["icons"]} == {"192x192", "512x512"}
        try:
            urllib.request.urlopen(f"{base}/static/icons/..%2Fdashboard.html")
            assert False
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()
    html = (Path(__file__).resolve().parents[1] / "drifts" / "dashboard.html").read_text()
    assert 'rel="apple-touch-icon"' in html and 'rel="manifest"' in html
