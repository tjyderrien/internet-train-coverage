"""Plain-language summary of one trip (standard library only, so it also runs on the phone).

Answers: how much of the time was the internet there, where and how long did it drop,
and how did it vary along the kilometres of the trip.
"""
import bisect
import json
import math

ROUND_S = 1.0          # tcp_rtt probes closer than this belong to one test round
MIN_OUTAGE_S = 4       # shorter gaps are not reported as outages
MAX_POS_GAP_S = 60     # do not guess a position across longer GPS gaps


def _hav(a, b):
    la1, lo1, la2, lo2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def _median(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def _nice_step(total, target_bins=80):
    raw = max(total / target_bins, 0.1)
    for s in (0.1, 0.2, 0.5, 1, 2, 5, 10, 20):
        if s >= raw:
            return s
    return 50


class Track:
    """Distance travelled (km) as a function of time, from the trip's own GPS fixes."""

    def __init__(self, positions):
        self.t, self.km = [], []
        prev, dist, rejected = None, 0.0, 0
        for p in positions:
            cur = (p["lat"], p["lon"])
            if prev is not None:
                d = _hav(prev[1], cur)
                dt = p["t"] - prev[0]
                if dt <= 0:
                    continue
                if d / dt > 120 and rejected < 3:   # GPS glitch (> 430 km/h): skip the fix,
                    rejected += 1                   # unless it persists: then the earlier
                    continue                        # fix was the bad one, so jump without
                if rejected < 3:                    # counting the distance
                    dist += d
                rejected = 0
            self.t.append(p["t"])
            self.km.append(dist / 1000)
            prev = (p["t"], cur)

    def __bool__(self):
        return len(self.t) >= 2

    @property
    def length_km(self):
        return self.km[-1] if self.km else 0.0

    def km_at(self, t):
        i = bisect.bisect_left(self.t, t)
        if i == 0 or i == len(self.t):
            edge = 0 if i == 0 else -1
            return self.km[edge] if abs(t - self.t[edge]) <= MAX_POS_GAP_S else None
        t0, t1 = self.t[i - 1], self.t[i]
        if min(t - t0, t1 - t) > MAX_POS_GAP_S:
            return None
        return self.km[i - 1] + (self.km[i] - self.km[i - 1]) * (t - t0) / (t1 - t0)


def _rounds(rows):
    """Group tcp_rtt probes into test rounds; a round is 'online' if any host answered."""
    out = []
    for r in rows:
        if r["probe"] != "tcp_rtt":
            continue
        if out and r["t"] - out[-1]["t"] < ROUND_S:
            out[-1]["ok"] = out[-1]["ok"] or bool(r["ok"])
        else:
            out.append({"t": r["t"], "ok": bool(r["ok"])})
    return out


def _bucket_stats(rows, rounds):
    vals = lambda p: [r["value"] for r in rows if r["probe"] == p and r["ok"]]
    return {
        "online": (sum(r["ok"] for r in rounds) / len(rounds)) if rounds else None,
        "rtt_ms": _median(vals("tcp_rtt")),
        "download_kbps": _median(vals("goodput")),
        "wifi_dbm": _median(vals("wifi")),
        "cell_dbm": _median(vals("cell")),
    }


def _bucketize(rows, rounds, key):
    groups = {}
    for r in rows:
        k = key(r["t"])
        if k is not None:
            groups.setdefault(k, ([], []))[0].append(r)
    for r in rounds:
        k = key(r["t"])
        if k is not None:
            groups.setdefault(k, ([], []))[1].append(r)
    return {k: _bucket_stats(*v) for k, v in sorted(groups.items())}


def summarize(db, sid):
    sess = db.query("SELECT * FROM sessions WHERE id=?", (sid,))
    if not sess:
        return None
    sess = sess[0]
    rows = db.query("SELECT t, probe, ok, value, bytes FROM measurements WHERE session_id=? "
                    "AND probe IN ('tcp_rtt','http204','goodput','wifi','cell') ORDER BY t", (sid,))
    used = db.query("SELECT COALESCE(SUM(bytes),0) b FROM measurements WHERE session_id=?",
                    (sid,))[0]["b"]
    track = Track(db.query("SELECT t, lat, lon FROM positions WHERE session_id=? ORDER BY t",
                           (sid,)))
    rounds = _rounds(rows)
    t0 = sess["started"]
    t1 = sess["ended"] or (rows[-1]["t"] if rows else t0)

    # outages: runs of failed rounds
    outages, start = [], None
    for i, r in enumerate(rounds):
        if not r["ok"] and start is None:
            start = r["t"]
        if start is not None and (r["ok"] or i == len(rounds) - 1):
            end = r["t"]
            if end - start >= MIN_OUTAGE_S:
                km = track.km_at(start) if track else None
                outages.append({"start": start, "duration_s": round(end - start),
                                "km": None if km is None else round(km, 1)})
            start = None

    # time chart: about 120 buckets, at least 10 s each
    step_s = max(10, int((t1 - t0) / 120))
    timeline = [{"t": t0 + k * step_s, **v}
                for k, v in _bucketize(rows, rounds, lambda t: int((t - t0) // step_s)).items()]

    along, step_km = None, None
    if track:
        step_km = _nice_step(track.length_km)
        def key(t):
            km = track.km_at(t)
            return None if km is None else int(km // step_km)
        along = [{"km0": round(k * step_km, 2), "km1": round((k + 1) * step_km, 2), **v}
                 for k, v in _bucketize(rows, rounds, key).items()]

    online = [r["ok"] for r in rounds]
    return {
        "trip": {"id": sid, "label": sess["label"], "started": t0, "ended": sess["ended"],
                 "platform": json.loads(sess["config"] or "{}").get("platform")},
        "summary": {
            "duration_s": round(t1 - t0),
            "online_pct": round(100 * sum(online) / len(online), 1) if online else None,
            "outages": len(outages),
            "longest_outage_s": max((o["duration_s"] for o in outages), default=0),
            "rtt_ms_median": _median(r["value"] for r in rows if r["probe"] == "tcp_rtt" and r["ok"]),
            "download_kbps_median": _median(r["value"] for r in rows if r["probe"] == "goodput" and r["ok"]),
            "distance_km": round(track.length_km, 1) if track else None,
            "data_used_mb": round(used / 1e6, 2),
        },
        "outages": outages,
        "timeline": {"step_s": step_s, "buckets": timeline},
        "along": None if along is None else {"step_km": step_km, "buckets": along},
    }


def list_trips(db):
    return db.query("""
        SELECT s.id, s.label, s.started, s.ended,
               (SELECT ROUND(100.0*AVG(ok),1) FROM measurements m
                 WHERE m.session_id=s.id AND m.probe='http204') AS online_pct,
               (SELECT COUNT(*) FROM positions p WHERE p.session_id=s.id) AS n_positions
        FROM sessions s ORDER BY s.started DESC""")
