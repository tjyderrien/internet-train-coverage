"""From raw time series to a 1D connectivity map along a route.

Pipeline: measurement time -> interpolated (lat, lon) -> chainage s along route [m]
-> bins of fixed length -> per-bin statistics, per trip and pooled.

Static vs. dynamic causes:
  * reproducibility: if dead zones sit at the same km on every trip, the cause is static
    (masts, tunnels, cuttings). Measured as the Spearman correlation of the bin profiles
    between pairs of trips.
  * drivers: Spearman correlation of per-(trip, bin) metrics with distance to the nearest
    cell tower, train speed, precipitation, cloud cover, hour of day.
"""
import csv
import json
import math
import urllib.request
from datetime import datetime, timezone

import numpy as np

R_EARTH = 6_371_000.0
MAX_POS_GAP_S = 60       # do not interpolate position across longer gaps
MAX_OFF_ROUTE_M = 500    # points farther from the route are discarded

# ---------------------------------------------------------------- geometry


def _xy(lat, lon, lat0, lon0):
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    x = np.radians(lon - lon0) * R_EARTH * math.cos(math.radians(lat0))
    y = np.radians(lat - lat0) * R_EARTH
    return x, y


class Route:
    def __init__(self, name, polyline):
        self.name = name
        self.pl = np.asarray(polyline, float)          # (M, 2) lat, lon
        self.lat0, self.lon0 = self.pl.mean(axis=0)
        x, y = _xy(self.pl[:, 0], self.pl[:, 1], self.lat0, self.lon0)
        self.ax, self.ay = x[:-1], y[:-1]
        self.dx, self.dy = np.diff(x), np.diff(y)
        self.seglen = np.hypot(self.dx, self.dy)
        self.cum = np.concatenate([[0], np.cumsum(self.seglen)])
        self.length = float(self.cum[-1])

    def project(self, lat, lon):
        """Return (chainage_m, cross_track_m) arrays for points."""
        px, py = _xy(lat, lon, self.lat0, self.lon0)
        s_out = np.full(px.shape, np.nan)
        d_out = np.full(px.shape, np.nan)
        L2 = np.maximum(self.seglen ** 2, 1e-9)
        for i in range(0, px.size, 2000):           # chunked to bound memory
            qx, qy = px[i:i + 2000, None], py[i:i + 2000, None]
            u = np.clip(((qx - self.ax) * self.dx + (qy - self.ay) * self.dy) / L2, 0, 1)
            d = np.hypot(self.ax + u * self.dx - qx, self.ay + u * self.dy - qy)
            k = d.argmin(axis=1)
            r = np.arange(k.size)
            s_out[i:i + 2000] = self.cum[k] + u[r, k] * self.seglen[k]
            d_out[i:i + 2000] = d[r, k]
        return s_out, d_out

    def point_at(self, s):
        s = np.clip(s, 0, self.length)
        lat = np.interp(s, self.cum, self.pl[:, 0])
        lon = np.interp(s, self.cum, self.pl[:, 1])
        return lat, lon


def simplify(points, min_step_m=50):
    """Thin a GPS track into a route polyline (drop points closer than min_step_m)."""
    out = [points[0]]
    for p in points[1:]:
        x, y = _xy([p[0]], [p[1]], out[-1][0], out[-1][1])
        if math.hypot(x[0], y[0]) >= min_step_m:
            out.append(p)
    return out


def save_route(db, name, polyline):
    r = Route(name, polyline)
    db.execute("INSERT OR REPLACE INTO routes VALUES (?,?,?)",
               (name, json.dumps([list(map(float, p)) for p in polyline]), r.length))
    return r


def load_route(db, name):
    row = db.query("SELECT * FROM routes WHERE name=?", (name,))
    if not row:
        raise KeyError(f"no route {name!r}")
    return Route(name, json.loads(row[0]["polyline"]))

# ---------------------------------------------------------------- positions


def locate(db, sid, t):
    """Interpolate lat/lon/speed at times t; NaN where no fix within MAX_POS_GAP_S."""
    pos = db.query("SELECT t, lat, lon, speed_mps FROM positions WHERE session_id=? "
                   "ORDER BY t", (sid,))
    t = np.asarray(t, float)
    if len(pos) < 2:
        nan = np.full(t.shape, np.nan)
        return nan, nan.copy(), nan.copy()
    pt = np.array([p["t"] for p in pos])
    lat = np.interp(t, pt, [p["lat"] for p in pos])
    lon = np.interp(t, pt, [p["lon"] for p in pos])
    sp = np.array([np.nan if p["speed_mps"] is None else p["speed_mps"] for p in pos])
    if np.isnan(sp).all():  # derive speed from fixes
        x, y = _xy([p["lat"] for p in pos], [p["lon"] for p in pos], pos[0]["lat"], pos[0]["lon"])
        v = np.hypot(np.diff(x), np.diff(y)) / np.maximum(np.diff(pt), 1e-3)
        sp = np.concatenate([[v[0]], v])
    speed = np.interp(t, pt, np.nan_to_num(sp))
    j = np.clip(np.searchsorted(pt, t), 1, len(pt) - 1)
    gap = np.minimum(np.abs(t - pt[j - 1]), np.abs(pt[j] - t))
    bad = (gap > MAX_POS_GAP_S) | (t < pt[0] - MAX_POS_GAP_S) | (t > pt[-1] + MAX_POS_GAP_S)
    for a in (lat, lon, speed):
        a[bad] = np.nan
    return lat, lon, speed

# ---------------------------------------------------------------- enrichment


def import_towers(db, csv_path, bbox=None):
    """Load an OpenCelliD-style CSV (radio,mcc,net,area,cell,unit,lon,lat,range,...)."""
    rows = []
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            lat, lon = float(r["lat"]), float(r["lon"])
            if bbox and not (bbox[0] <= lat <= bbox[2] and bbox[1] <= lon <= bbox[3]):
                continue
            rows.append((r.get("radio"), r.get("mcc"), r.get("net"), r.get("area"),
                         r.get("cell"), lat, lon, float(r.get("range") or 0)))
    with db.lock:
        db.conn.executemany("INSERT INTO towers VALUES (?,?,?,?,?,?,?,?)", rows)
        db.conn.commit()
    return len(rows)


def tower_stats(db, lat, lon, radius_m=2000):
    """Distance to nearest tower and count within radius, for points (lat, lon)."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    if lat.size == 0 or np.isnan(lat).all():
        return np.full(lat.shape, np.nan), np.zeros(lat.shape, int)
    pad = 0.2
    tw = db.query("SELECT lat, lon FROM towers WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?",
                  (np.nanmin(lat) - pad, np.nanmax(lat) + pad,
                   np.nanmin(lon) - pad * 1.6, np.nanmax(lon) + pad * 1.6))
    if not tw:
        return np.full(lat.shape, np.nan), np.zeros(lat.shape, int)
    tlat = np.array([t["lat"] for t in tw])
    tlon = np.array([t["lon"] for t in tw])
    lat0, lon0 = np.nanmean(lat), np.nanmean(lon)
    tx, ty = _xy(tlat, tlon, lat0, lon0)
    px, py = _xy(lat, lon, lat0, lon0)
    d = np.hypot(px[:, None] - tx, py[:, None] - ty)
    return d.min(axis=1), (d < radius_m).sum(axis=1)


WEATHER_VARS = "precipitation,rain,snowfall,cloud_cover,relative_humidity_2m,temperature_2m,wind_speed_10m"


def weather(db, lat, lon, t):
    """Hourly weather from Open-Meteo (no key), cached on a 0.1 deg x 1 h grid."""
    lat_r, lon_r, hour = round(lat, 1), round(lon, 1), int(t // 3600)
    hit = db.query("SELECT data FROM weather WHERE lat_r=? AND lon_r=? AND hour=?",
                   (lat_r, lon_r, hour))
    if hit:
        return json.loads(hit[0]["data"])
    day = datetime.fromtimestamp(t, timezone.utc).date().isoformat()
    age_days = (datetime.now(timezone.utc).timestamp() - t) / 86400
    host = "archive-api.open-meteo.com/v1/archive" if age_days > 7 else "api.open-meteo.com/v1/forecast"
    url = (f"https://{host}?latitude={lat_r}&longitude={lon_r}&start_date={day}"
           f"&end_date={day}&hourly={WEATHER_VARS}&timezone=UTC")
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            h = json.loads(r.read())["hourly"]
    except Exception:
        return {}
    out = None
    for i, ts in enumerate(h["time"]):
        hr = int(datetime.fromisoformat(ts).replace(tzinfo=timezone.utc).timestamp() // 3600)
        rec = {k: h[k][i] for k in h if k != "time"}
        db.execute("INSERT OR REPLACE INTO weather VALUES (?,?,?,?)",
                   (lat_r, lon_r, hr, json.dumps(rec)))
        if hr == hour:
            out = rec
    return out or {}

# ---------------------------------------------------------------- profile


def _stats(rows):
    """Summary metrics for a list of measurement dicts falling in one bin."""
    def vals(probe, ok=True):
        return np.array([r["value"] for r in rows
                         if r["probe"] == probe and r["ok"] and r["value"] is not None])

    def frac_ok(probe):
        sel = [r["ok"] for r in rows if r["probe"] == probe
               and not (r["extra"] and "skipped" in r["extra"])]
        return float(np.mean(sel)) if sel else None

    def med(a):
        return float(np.median(a)) if a.size else None

    def p90(a):
        return float(np.percentile(a, 90)) if a.size else None

    rtt, gp, rssi = vals("tcp_rtt"), vals("goodput"), vals("wifi")
    loss = frac_ok("tcp_rtt")
    return {
        "n": len(rows),
        "online": frac_ok("http204"),               # 0..1, real internet reachable
        "rtt_ms_median": med(rtt), "rtt_ms_p90": p90(rtt),
        "loss": None if loss is None else 1 - loss,   # failed TCP handshakes
        "dns_ms_median": med(vals("dns")),
        "goodput_kbps_median": med(gp), "goodput_n": int(gp.size),
        "wifi_rssi_median": med(rssi),
        "cell_dbm_median": med(vals("cell")),       # phone's own serving cell (Android)
    }


def session_points(db, sid, route):
    rows = db.query("SELECT t, probe, target, ok, value, extra FROM measurements "
                    "WHERE session_id=? AND probe IN ('tcp_rtt','http204','dns','goodput','wifi','cell') "
                    "ORDER BY t", (sid,))
    if not rows:
        return []
    t = np.array([r["t"] for r in rows])
    lat, lon, speed = locate(db, sid, t)
    s, d = route.project(np.nan_to_num(lat), np.nan_to_num(lon))
    keep = ~np.isnan(lat) & (d <= MAX_OFF_ROUTE_M)
    out = []
    for i in np.flatnonzero(keep):
        r = dict(rows[i])
        r["extra"] = json.loads(r["extra"]) if r["extra"] else None
        r.update(s=float(s[i]), lat=float(lat[i]), lon=float(lon[i]),
                 speed_mps=float(speed[i]))
        out.append(r)
    return out


def profile(db, route_name, session_ids=None, bin_m=500, with_weather=True):
    route = load_route(db, route_name)
    if session_ids is None:
        session_ids = [r["id"] for r in db.query("SELECT id FROM sessions ORDER BY id")]
    nb = max(1, math.ceil(route.length / bin_m))
    centers = (np.arange(nb) + 0.5) * bin_m
    clat, clon = route.point_at(centers)
    tdist, tcount = tower_stats(db, clat, clon)

    per = {}   # sid -> bin -> rows
    for sid in session_ids:
        for p in session_points(db, sid, route):
            b = min(int(p["s"] // bin_m), nb - 1)
            per.setdefault(sid, {}).setdefault(b, []).append(p)

    bins = []
    for b in range(nb):
        pooled = [p for sid in per for p in per[sid].get(b, [])]
        trips = {}
        for sid in per:
            rows = per[sid].get(b)
            if not rows:
                continue
            st = _stats(rows)
            tm = float(np.mean([r["t"] for r in rows]))
            st["t_mean"] = tm
            st["speed_kmh"] = float(np.nanmean([r["speed_mps"] for r in rows]) * 3.6)
            if with_weather:
                st["weather"] = weather(db, float(clat[b]), float(clon[b]), tm)
            trips[str(sid)] = st
        bins.append({
            "bin": b, "s0_m": b * bin_m, "s1_m": min((b + 1) * bin_m, route.length),
            "lat": float(clat[b]), "lon": float(clon[b]),
            "tower_nearest_m": None if np.isnan(tdist[b]) else float(tdist[b]),
            "towers_2km": int(tcount[b]),
            "all": _stats(pooled) if pooled else None,
            "trips": trips,
        })
    return {"route": route_name, "length_m": route.length, "bin_m": bin_m,
            "sessions": session_ids, "bins": bins}

# ---------------------------------------------------------------- correlations


def _spearman(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = ~np.isnan(a) & ~np.isnan(b)
    if m.sum() < 5:
        return None, int(m.sum())
    ra = np.argsort(np.argsort(a[m])).astype(float)
    rb = np.argsort(np.argsort(b[m])).astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return None, int(m.sum())
    return float(np.corrcoef(ra, rb)[0, 1]), int(m.sum())


METRICS = ["online", "rtt_ms_median", "loss", "goodput_kbps_median", "cell_dbm_median"]


def correlations(prof):
    """Answer 'static or dynamic?' from a profile dict."""
    nan = float("nan")

    def g(d, k):
        v = d.get(k) if d else None
        return nan if v is None else v

    sids = [str(s) for s in prof["sessions"]]
    # 1) trip-to-trip reproducibility of the spatial pattern
    repro = {}
    for m in METRICS:
        rs = []
        for i, a in enumerate(sids):
            for b in sids[i + 1:]:
                va = [g(x["trips"].get(a), m) for x in prof["bins"]]
                vb = [g(x["trips"].get(b), m) for x in prof["bins"]]
                r, n = _spearman(va, vb)
                if r is not None:
                    rs.append({"a": a, "b": b, "rho": r, "n_bins": n})
        repro[m] = {"pairs": rs,
                    "mean_rho": float(np.mean([x["rho"] for x in rs])) if rs else None}
    # 2) drivers, over all (trip, bin) cells
    cells = [(x, st) for x in prof["bins"] for st in x["trips"].values()]
    drivers = {
        "tower_nearest_m": [g(x, "tower_nearest_m") for x, _ in cells],
        "cell_dbm": [g(st, "cell_dbm_median") for _, st in cells],
        "speed_kmh": [g(st, "speed_kmh") for _, st in cells],
        "precipitation": [g(st.get("weather"), "precipitation") for _, st in cells],
        "cloud_cover": [g(st.get("weather"), "cloud_cover") for _, st in cells],
        "relative_humidity_2m": [g(st.get("weather"), "relative_humidity_2m") for _, st in cells],
        "hour_utc": [(st["t_mean"] // 3600) % 24 for _, st in cells],
    }
    out = {}
    for m in METRICS:
        y = [g(st, m) for _, st in cells]
        out[m] = {}
        for k, x in drivers.items():
            r, n = _spearman(x, y)
            out[m][k] = {"rho": r, "n": n}
    return {"reproducibility": repro, "drivers": out,
            "note": "Spearman rho. High trip-to-trip rho = static (infrastructure) "
                    "pattern. Weather rho is confounded with route position unless many "
                    "trips are recorded in different weather."}
