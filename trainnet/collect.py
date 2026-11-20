"""Recorder: runs each probe on its own timer and writes to SQLite.

A data budget caps the uplink bytes per hour; when exceeded, goodput tests pause first.
"""
import json
import socket
import threading
import time

from . import probes
from .position import read_portal

DEFAULTS = {
    "wifi_s": 2,
    "rtt_s": 2,
    "rtt_hosts": ["1.1.1.1", "8.8.8.8", "9.9.9.9"],
    "http204_s": 10,
    "dns_s": 10,
    "goodput_s": 120,
    "goodput_bytes": 100_000,
    "portal_s": 5,
    "portal_url": None,
    "budget_mb_per_h": 10.0,
}


class Recorder:
    def __init__(self, db, label=None, **cfg):
        self.db = db
        self.cfg = {**DEFAULTS, **{k: v for k, v in cfg.items() if v is not None}}
        self.stop = threading.Event()
        self.bytes_window = []  # (t, nbytes) over the last hour
        self.bytes_lock = threading.Lock()
        self.sid = db.execute(
            "INSERT INTO sessions(started,label,host,config) VALUES (?,?,?,?)",
            (time.time(), label, socket.gethostname(), json.dumps(self.cfg))).lastrowid
        self.last = {}

    def used_mb_last_hour(self):
        now = time.time()
        with self.bytes_lock:
            self.bytes_window = [(t, b) for t, b in self.bytes_window if now - t < 3600]
            return sum(b for _, b in self.bytes_window) / 1e6

    def _store(self, r):
        t = time.time()
        nb = r.get("nbytes", 0)
        if nb:
            with self.bytes_lock:
                self.bytes_window.append((t, nb))
        self.db.add_measurement(self.sid, t, r["probe"], r.get("target"), r["ok"],
                                r.get("value"), nb, r.get("extra"))
        self.last[(r["probe"], r.get("target"))] = (t, r)

    def _loop(self, period, fn):
        nxt = time.monotonic()
        while not self.stop.is_set():
            try:
                fn()
            except Exception as e:  # never let one probe kill the run
                self._store(dict(probe="error", target=fn.__name__, ok=False,
                                 extra={"err": repr(e)}))
            nxt += period
            self.stop.wait(max(0.0, nxt - time.monotonic()))

    # -- probe tasks -----------------------------------------------------------
    def do_wifi(self):
        self._store(probes.wifi())

    def do_rtt(self):
        for h in self.cfg["rtt_hosts"]:
            self._store(probes.tcp_rtt(h))

    def do_http204(self):
        self._store(probes.http204())

    def do_dns(self):
        self._store(probes.dns())

    def do_goodput(self):
        if self.used_mb_last_hour() > 0.7 * self.cfg["budget_mb_per_h"]:
            self._store(dict(probe="goodput", target="cloudflare", ok=False,
                             extra={"skipped": "budget"}))
            return
        self._store(probes.goodput(self.cfg["goodput_bytes"]))

    def do_portal(self):
        try:
            pos, raw = read_portal(self.cfg["portal_url"])
        except Exception as e:
            self._store(dict(probe="portal", target=self.cfg["portal_url"], ok=False,
                             extra={"err": type(e).__name__}))
            return
        if pos:
            self.db.add_position(self.sid, time.time(), pos["lat"], pos["lon"],
                                 pos["speed_mps"], "portal", raw)
        self._store(dict(probe="portal", target=self.cfg["portal_url"], ok=pos is not None))

    def run(self, duration_s=None, on_tick=None):
        c = self.cfg
        tasks = [(c["wifi_s"], self.do_wifi), (c["rtt_s"], self.do_rtt),
                 (c["http204_s"], self.do_http204), (c["dns_s"], self.do_dns),
                 (c["goodput_s"], self.do_goodput)]
        if c["portal_url"]:
            tasks.append((c["portal_s"], self.do_portal))
        threads = [threading.Thread(target=self._loop, args=a, daemon=True) for a in tasks]
        for th in threads:
            th.start()
        t_end = time.time() + duration_s if duration_s else None
        try:
            while not self.stop.is_set() and (t_end is None or time.time() < t_end):
                time.sleep(1)
                if on_tick:
                    on_tick(self)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop.set()
            for th in threads:
                th.join(timeout=20)
            self.db.execute("UPDATE sessions SET ended=? WHERE id=?", (time.time(), self.sid))
        return self.sid
