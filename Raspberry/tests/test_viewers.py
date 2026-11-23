"""--viewers (symposium): visitors get a view-only page; only this computer controls the demo."""

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drifts.live as live
from drifts.live import build_parser, make_handler
from test_privacy import make


def serve(p):
    p.camera.face.pop("secret_image", None)            # the shared fake camera's raw-bytes marker
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(p))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def post(url):
    try:
        return urllib.request.urlopen(urllib.request.Request(url, method="POST")).status
    except urllib.error.HTTPError as e:
        return e.code


def get(url):
    try:
        r = urllib.request.urlopen(url, timeout=3)
        return r.status, r
    except urllib.error.HTTPError as e:
        return e.code, None


def test_flags_default_off():
    a = build_parser().parse_args([])
    assert a.viewers is False and a.max_viewers == 20 and a.no_video_for_viewers is False


def test_visitors_cannot_control(tmp_path, monkeypatch):
    p = make(tmp_path)
    p.args.viewers, p.args.max_viewers, p.args.no_video_for_viewers = True, 20, False
    sent = []
    p.link.send_command = sent.append
    monkeypatch.setattr(live, "LOCAL_ADDRS", ())        # every client counts as a visitor
    srv, base = serve(p)
    try:
        for path in ("/beep", "/calibrate", "/capture?label=open", "/test?what=buzzer", "/eye_send?on=0",
                     "/glasses?mode=on", "/guided?action=cancel", "/name/record?name=x"):
            assert post(base + path) == 403, path
        st = json.loads(urllib.request.urlopen(base + "/status.json").read())
        assert st["viewer"] is True and st["viewer_video"] is True and "profile_info" not in st
        d = json.loads(urllib.request.urlopen(base + "/drivers.json").read())
        assert d["drivers"] == [] and d["driver"] is None
    finally:
        srv.shutdown()
    assert sent == []                                      # nothing reached the ESP32


def test_this_computer_keeps_control(tmp_path):
    p = make(tmp_path)
    p.args.viewers, p.args.max_viewers, p.args.no_video_for_viewers = True, 20, False
    srv, base = serve(p)                                   # 127.0.0.1 = the presenter
    try:
        assert post(base + "/calibrate") == 200
        st = json.loads(urllib.request.urlopen(base + "/status.json").read())
        assert "viewer" not in st and st["viewers"] == 0
    finally:
        srv.shutdown()


def test_viewer_cap_and_no_video(tmp_path, monkeypatch):
    p = make(tmp_path)
    p.args.viewers, p.args.max_viewers, p.args.no_video_for_viewers = True, 0, False
    monkeypatch.setattr(live, "LOCAL_ADDRS", ())
    srv, base = serve(p)
    try:
        assert get(base + "/stream.mjpg")[0] == 503         # cap reached
        p.args.no_video_for_viewers = True
        assert get(base + "/stream.mjpg")[0] == 403         # camera off for visitors
    finally:
        srv.shutdown()


def test_page_has_view_only_mode():
    html = (Path(__file__).resolve().parents[1] / "drifts" / "dashboard.html").read_text()
    assert "applyViewer(s)" in html and "VIEW ONLY" in html and "#alarmcard" in html
