"""Android sources via Termux:API (needs the Termux and Termux:API apps, plus `pkg install termux-api`).

Gives three things a laptop cannot:
  * GPS on the same device (no portal or GPX merge needed),
  * the phone's own cellular signal (serving cell RSRP/RSRQ, cell id, neighbours). This is
    the coverage outside the train: compare it with the internet quality through train Wi-Fi,
  * Wi-Fi RSSI and BSSID of the train access point.
"""
import json
import shutil
import subprocess
import threading
import time

INVALID = 2**31 - 1   # Android's CellInfo.UNAVAILABLE


def available():
    return shutil.which("termux-location") is not None


def _run(cmd, timeout=15):
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    return json.loads(out) if out.strip() else None


def _clean(v):
    return None if v is None or not isinstance(v, (int, float)) or abs(v) >= 10_000 else v


def wifi():
    try:
        d = _run(["termux-wifi-connectioninfo"]) or {}
    except Exception as e:
        return dict(probe="wifi", target="wlan0", ok=False, extra={"err": type(e).__name__})
    rssi = _clean(d.get("rssi"))
    ok = d.get("supplicant_state") == "COMPLETED" and rssi is not None and rssi > -127
    return dict(probe="wifi", target="wlan0", ok=ok, value=rssi if ok else None,
                extra={"ssid": d.get("ssid"), "bssid": d.get("bssid"),
                       "tx_rate_mbps": d.get("link_speed_mbps"),
                       "freq_mhz": d.get("frequency_mhz"),
                       "state": d.get("supplicant_state")})


def _level(c):
    """Best signal-strength number for a cell: RSRP (LTE), SS-RSRP (5G), else dBm."""
    for k in ("rsrp", "ss_rsrp", "csi_rsrp", "dbm"):
        v = _clean(c.get(k))
        if v is not None:
            return v
    return None


def cell():
    """Serving cell + neighbours. value = RSRP/dBm of the serving cell."""
    try:
        cells = _run(["termux-telephony-cellinfo"]) or []
    except Exception as e:
        return dict(probe="cell", target=None, ok=False, extra={"err": type(e).__name__})
    serving = [c for c in cells if c.get("registered")]
    neigh = [{"type": c.get("type"), "pci": c.get("pci"), "level": _level(c)}
             for c in cells if not c.get("registered")]
    if not serving:
        return dict(probe="cell", target=None, ok=False, extra={"neighbours": neigh})
    s = {k: None if v in (INVALID, 2**63 - 1) else v for k, v in serving[0].items()}
    return dict(probe="cell", target=s.get("type"), ok=True, value=_level(serving[0]),
                extra={"serving": s, "neighbours": neigh})


def radio():
    """Network type (lte/nr/...), data state, operator. Cheap, every ~30 s."""
    try:
        d = _run(["termux-telephony-deviceinfo"]) or {}
    except Exception as e:
        return dict(probe="radio", target=None, ok=False, extra={"err": type(e).__name__})
    keep = ("data_network_type", "network_type", "data_state", "data_activity",
            "network_operator_name", "network_roaming", "sim_operator_name")
    return dict(probe="radio", target=d.get("data_network_type") or d.get("network_type"),
                ok=d.get("data_state") == "connected", extra={k: d.get(k) for k in keep})


class GpsStream(threading.Thread):
    """Streams GPS fixes from `termux-location -r updates` into the positions table.
    Falls back to repeated single fixes if streaming is not supported."""

    def __init__(self, db, sid, stop):
        super().__init__(daemon=True)
        self.db, self.sid, self.stop = db, sid, stop
        self.n = 0
        self.last = None

    def _store(self, d):
        if not d or "latitude" not in d:
            return
        t = time.time()
        age = d.get("elapsedMs")
        if isinstance(age, (int, float)) and 0 <= age < 60_000:
            t -= age / 1000
        self.db.add_position(self.sid, t, d["latitude"], d["longitude"], d.get("speed"), "gps",
                             {k: d.get(k) for k in ("accuracy", "altitude", "bearing", "provider")})
        self.n += 1
        self.last = (t, d)

    def run(self):
        dec = json.JSONDecoder()
        try:
            p = subprocess.Popen(["termux-location", "-p", "gps", "-r", "updates"],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
            buf = ""
            while not self.stop.is_set():
                line = p.stdout.readline()
                if not line:
                    break
                buf += line
                try:
                    obj, end = dec.raw_decode(buf.lstrip())
                    buf = buf.lstrip()[end:]
                    self._store(obj)
                except json.JSONDecodeError:
                    pass
            p.terminate()
        except Exception:
            pass
        while not self.stop.is_set():          # fallback: one fix at a time
            try:
                self._store(_run(["termux-location", "-p", "gps", "-r", "once"], timeout=40))
            except Exception:
                pass
            self.stop.wait(3)


def wake_lock(on=True):
    """Keep the CPU awake with the screen off (Android would otherwise pause Termux)."""
    cmd = "termux-wake-lock" if on else "termux-wake-unlock"
    if shutil.which(cmd):
        subprocess.run([cmd], timeout=10)
