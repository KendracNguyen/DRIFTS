"""Small HTTP API for the mobile app's Live mode: GET /api/status."""

from __future__ import annotations

import logging
import threading

from flask import Flask, jsonify

log = logging.getLogger(__name__)


class SharedState:
    """Latest readings, written by the control loop and read by Flask."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data = {
            "perclos": None,
            "steering": None,
            "obd": None,           # display-only, not used in the score
            "score": None,
            "level": "OK",
            "alerts": {"seat": False, "audio": False, "voice": False},
            "esp32_connected": False,
        }

    def update(self, **values):
        with self._lock:
            self._data.update(values)

    def snapshot(self) -> dict:
        with self._lock:
            return {**self._data, "alerts": dict(self._data["alerts"])}


def create_app(state: SharedState) -> Flask:
    app = Flask(__name__)

    @app.after_request
    def allow_app(resp):
        # The app prototype is opened as a local file, so allow cross-origin reads.
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @app.get("/api/status")
    def status():
        return jsonify(state.snapshot())

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True})

    return app


def start_api_thread(state: SharedState, host: str, port: int) -> threading.Thread:
    logging.getLogger("werkzeug").setLevel(logging.WARNING)  # no log line per poll
    app = create_app(state)
    t = threading.Thread(
        target=lambda: app.run(host=host, port=port, threaded=True, use_reloader=False),
        name="api", daemon=True,
    )
    t.start()
    log.info("Status API on http://%s:%d/api/status", host, port)
    return t
