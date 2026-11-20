"""Read-only HTTP API for the frontend. Run: trainnet serve  ->  http://127.0.0.1:8765/docs"""
import json
import os
import time

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from . import analyze
from .db import DB, DEFAULT_DB

app = FastAPI(title="trainnet", version="0.1",
              description="Wi-Fi and internet quality along train trips.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"],
                   allow_headers=["*"])
db = DB(os.environ.get("TRAINNET_DB", DEFAULT_DB))


def _ids(s):
    return [int(x) for x in s.split(",")] if s else None


@app.get("/sessions")
def sessions():
    """All recorded trips with counts and data used."""
    return db.query("""
        SELECT s.id, s.label, s.started, s.ended, s.host,
               (SELECT COUNT(*) FROM measurements m WHERE m.session_id=s.id) AS n_measurements,
               (SELECT COUNT(*) FROM positions p WHERE p.session_id=s.id) AS n_positions,
               (SELECT COALESCE(SUM(bytes),0) FROM measurements m WHERE m.session_id=s.id) AS bytes_used
        FROM sessions s ORDER BY s.started DESC""")


@app.get("/sessions/{sid}/timeseries")
def timeseries(sid: int, probe: str | None = None, since: float | None = None):
    """Raw measurements (time domain). probe in wifi|tcp_rtt|http204|dns|goodput|portal|cell|radio."""
    sql = "SELECT t, probe, target, ok, value, bytes, extra FROM measurements WHERE session_id=?"
    args = [sid]
    if probe:
        sql += " AND probe=?"
        args.append(probe)
    if since:
        sql += " AND t>?"
        args.append(since)
    rows = db.query(sql + " ORDER BY t", args)
    for r in rows:
        r["extra"] = json.loads(r["extra"]) if r["extra"] else None
    return rows


@app.get("/sessions/{sid}/positions")
def positions(sid: int):
    return db.query("SELECT t, lat, lon, speed_mps, source FROM positions "
                    "WHERE session_id=? ORDER BY t", (sid,))


@app.get("/live")
def live(window_s: int = 60):
    """Latest trip, last `window_s` seconds: for a live dashboard during recording."""
    s = db.query("SELECT * FROM sessions ORDER BY started DESC LIMIT 1")
    if not s:
        raise HTTPException(404, "no sessions")
    s = s[0]
    rows = db.query("SELECT t, probe, target, ok, value, extra FROM measurements "
                    "WHERE session_id=? AND t>? ORDER BY t", (s["id"], time.time() - window_s))
    for r in rows:
        r["extra"] = json.loads(r["extra"]) if r["extra"] else None
    pos = db.query("SELECT t, lat, lon, speed_mps FROM positions WHERE session_id=? "
                   "ORDER BY t DESC LIMIT 1", (s["id"],))
    return {"session": s, "recording": s["ended"] is None, "summary": analyze._stats(rows),
            "position": pos[0] if pos else None, "measurements": rows}


@app.get("/routes")
def routes():
    return db.query("SELECT name, length_m FROM routes")


@app.get("/routes/{name}")
def route(name: str):
    r = db.query("SELECT * FROM routes WHERE name=?", (name,))
    if not r:
        raise HTTPException(404)
    return {"name": name, "length_m": r[0]["length_m"], "polyline": json.loads(r[0]["polyline"])}


@app.get("/routes/{name}/profile")
def profile(name: str, sessions: str | None = Query(None, description="e.g. 1,2,5"),
            bin_m: int = 500, weather: bool = True):
    """1D map: metrics per bin of chainage, pooled ('all') and per trip ('trips')."""
    try:
        return analyze.profile(db, name, _ids(sessions), bin_m, weather)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.get("/routes/{name}/correlations")
def correlations(name: str, sessions: str | None = None, bin_m: int = 500):
    """Static vs. dynamic: trip-to-trip reproducibility and Spearman rho vs. drivers."""
    return analyze.correlations(analyze.profile(db, name, _ids(sessions), bin_m, True))


@app.get("/towers")
def towers(lat0: float, lon0: float, lat1: float, lon1: float, limit: int = 5000):
    return db.query("SELECT radio, mcc, net, lat, lon, range_m FROM towers WHERE lat BETWEEN ? "
                    "AND ? AND lon BETWEEN ? AND ? LIMIT ?", (lat0, lat1, lon0, lon1, limit))
