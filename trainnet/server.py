"""The app: a small local web server with a Start/Stop page. Standard library only.

Listens on 127.0.0.1 only, so other passengers on the train Wi-Fi cannot reach it.
"""
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import android, trip
from .collect import Recorder
from .db import export
from .position import PORTAL_PRESETS

PAGE = Path(__file__).with_name("web") / "index.html"


class App:
    def __init__(self, db):
        self.db = db
        self.rec = None
        self.thread = None
        self.is_android = android.available()
        self.lock = threading.Lock()

    def start(self, label=None, portal=None):
        with self.lock:
            if self.rec and not self.rec.stop.is_set():
                return {"error": "already recording"}
            url = PORTAL_PRESETS.get(portal, portal) or None
            label = label or time.strftime("Trip %Y-%m-%d %H:%M")
            self.rec = Recorder(self.db, label, android=self.is_android, portal_url=url)
            self.thread = threading.Thread(target=self._run, args=(self.rec,), daemon=True)
            self.thread.start()
            return {"trip_id": self.rec.sid}

    def _run(self, rec):
        rec.run()
        shared = Path.home() / "storage" / "shared"
        if self.is_android and shared.exists():   # phone: copy so it can be moved to a computer
            try:
                export(self.db, shared / "Documents" / "trainnet" / "trainnet.sqlite")
            except Exception:
                pass

    def stop(self):
        with self.lock:
            rec = self.rec
        if not rec or rec.stop.is_set():
            return {"error": "not recording"}
        rec.stop.set()
        self.thread.join(timeout=30)
        return {"trip_id": rec.sid}

    def status(self):
        rec = self.rec
        recording = bool(rec and not rec.stop.is_set())
        out = {"recording": recording, "android": self.is_android,
               "portals": list(PORTAL_PRESETS)}
        if not recording:
            return out

        def last(probe, max_age=15):
            best = None
            for (p, _), (t, r) in list(rec.last.items()):
                if p == probe and time.time() - t < max_age and (best is None or t > best[0]):
                    best = (t, r)
            return best[1] if best else None

        rtts = [r for (p, _), (t, r) in list(rec.last.items())
                if p == "tcp_rtt" and time.time() - t < 6]
        ok_rtts = [r["value"] for r in rtts if r["ok"]]
        g, w, c = last("goodput", 300), last("wifi"), last("cell")
        gps = rec.gps.last if rec.gps else None
        pos = self.db.query("SELECT lat, lon, speed_mps FROM positions WHERE session_id=? "
                            "ORDER BY t DESC LIMIT 1", (rec.sid,))
        speed = pos[0]["speed_mps"] if pos else None
        out.update({
            "trip_id": rec.sid,
            "label": self.db.query("SELECT label FROM sessions WHERE id=?", (rec.sid,))[0]["label"],
            "elapsed_s": round(time.time() - self.db.query(
                "SELECT started FROM sessions WHERE id=?", (rec.sid,))[0]["started"]),
            "online": bool(ok_rtts) if rtts else None,
            "rtt_ms": min(ok_rtts) if ok_rtts else None,
            "download_kbps": g["value"] if g and g["ok"] else None,
            "wifi_dbm": w["value"] if w and w["ok"] else None,
            "cell_dbm": c["value"] if c and c["ok"] else None,
            "speed_kmh": None if speed is None else round(speed * 3.6),
            "has_position": bool(pos),
            "gps_fixes": rec.gps.n if rec.gps else None,
            "data_mb_last_hour": round(rec.used_mb_last_hour(), 2),
        })
        return out


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/status":
                return self._send(200, app.status())
            if path == "/api/trips":
                return self._send(200, trip.list_trips(app.db))
            m = re.fullmatch(r"/api/trips/(\d+)", path)
            if m:
                s = trip.summarize(app.db, int(m[1]))
                return self._send(200 if s else 404, s or {"error": "no such trip"})
            m = re.fullmatch(r"/api/trips/(\d+)/raw", path)
            if m:
                rows = app.db.query("SELECT t, probe, target, ok, value, bytes, extra "
                                    "FROM measurements WHERE session_id=? ORDER BY t", (int(m[1]),))
                pos = app.db.query("SELECT t, lat, lon, speed_mps, source FROM positions "
                                   "WHERE session_id=? ORDER BY t", (int(m[1]),))
                for r in rows:
                    r["extra"] = json.loads(r["extra"]) if r["extra"] else None
                return self._send(200, {"measurements": rows, "positions": pos})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                body = {}
            if self.path == "/api/start":
                r = app.start(body.get("label"), body.get("portal"))
            elif self.path == "/api/stop":
                r = app.stop()
            else:
                return self._send(404, {"error": "not found"})
            self._send(409 if "error" in r else 200, r)

    return Handler


def serve(db, port=8765, open_browser=True):
    app = App(db)
    srv = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app))
    url = f"http://127.0.0.1:{port}"
    print(f"Train internet is running: open {url}   (Ctrl-C to quit)")
    if open_browser:
        threading.Timer(0.5, _open, args=(url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if app.rec and not app.rec.stop.is_set():
            print("stopping the recording...")
            app.stop()
        srv.server_close()


def _open(url):
    import shutil
    import subprocess
    import webbrowser
    if shutil.which("termux-open-url"):
        subprocess.run(["termux-open-url", url])
    else:
        webbrowser.open(url)
