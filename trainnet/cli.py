"""Command line: trainnet record | portal-test | gpx | route | towers | profile | serve | export | merge"""
import argparse
import json
import sys
import time
from pathlib import Path

from .db import DB, DEFAULT_DB, export, merge
from .position import PORTAL_PRESETS, read_gpx, read_portal


def _status(rec):
    if int(time.time()) % 5:
        return
    def last(p, tgt=None):
        for (probe, target), (_, r) in rec.last.items():
            if probe == p and (tgt is None or target == tgt):
                return r
    w, r, h, g = last("wifi"), last("tcp_rtt", "1.1.1.1"), last("http204"), last("goodput")
    c = last("cell")
    fmt = lambda x, f: f.format(x) if x is not None else "  -  "
    line = (f"\rrssi {fmt(w and w.get('value'), '{:4.0f}')} dBm | "
            f"rtt {fmt(r and r.get('value'), '{:6.0f}')} ms | "
            f"online {'yes' if h and h['ok'] else 'NO '} | "
            f"goodput {fmt(g and g.get('value'), '{:7.0f}')} kbit/s | "
            f"used {rec.used_mb_last_hour():5.2f} MB/h")
    if rec.android:
        gps = rec.gps.last if rec.gps else None
        spd = gps and gps[1].get("speed")
        line += (f" | cell {fmt(c and c.get('value'), '{:4.0f}')} dBm {(c or {}).get('target') or ''}"
                 f" | gps {rec.gps.n if rec.gps else 0} fixes"
                 f" {fmt(spd and spd * 3.6, '{:3.0f}')} km/h")
    line += "   "
    sys.stdout.write(line)
    sys.stdout.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(prog="trainnet")
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("record", help="record a trip (Ctrl-C to stop)")
    p.add_argument("--label")
    p.add_argument("--portal", help=f"URL or preset: {', '.join(PORTAL_PRESETS)}")
    p.add_argument("--duration", type=float, help="seconds")
    p.add_argument("--goodput-every", type=float, dest="goodput_s")
    p.add_argument("--goodput-bytes", type=int)
    p.add_argument("--rtt-every", type=float, dest="rtt_s")
    p.add_argument("--budget", type=float, dest="budget_mb_per_h", help="MB per hour cap")
    p.add_argument("--android", action=argparse.BooleanOptionalAction, default=None,
                   help="use Termux:API (GPS, cell, Wi-Fi); auto-detected")
    p.add_argument("--export", help="copy the DB here when the trip ends "
                   "(Android default: ~/storage/shared/Documents/trainnet/)")

    p = sub.add_parser("export", help="copy the DB to a file (e.g. phone shared storage)")
    p.add_argument("dest")

    p = sub.add_parser("merge", help="import all sessions from another DB (e.g. the phone's)")
    p.add_argument("src")

    p = sub.add_parser("portal-test", help="fetch an onboard portal URL and show what is found")
    p.add_argument("url")

    p = sub.add_parser("gpx", help="attach a phone GPX track to a session")
    p.add_argument("session", type=int)
    p.add_argument("file")

    p = sub.add_parser("route", help="define a route from a session's track or a GPX")
    p.add_argument("name")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--session", type=int)
    g.add_argument("--gpx")

    p = sub.add_parser("towers", help="import OpenCelliD CSV (optionally bbox lat0,lon0,lat1,lon1)")
    p.add_argument("csv")
    p.add_argument("--bbox")

    p = sub.add_parser("profile", help="print 1D profile + correlations as JSON")
    p.add_argument("route")
    p.add_argument("--sessions")
    p.add_argument("--bin", type=int, default=500)
    p.add_argument("--no-weather", action="store_true")

    p = sub.add_parser("serve", help="start the HTTP API for the frontend")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1")

    a = ap.parse_args(argv)
    db = DB(a.db)

    if a.cmd == "record":
        from . import android
        from .collect import Recorder
        url = PORTAL_PRESETS.get(a.portal, a.portal)
        is_android = android.available() if a.android is None else a.android
        rec = Recorder(db, a.label, android=is_android, portal_url=url, goodput_s=a.goodput_s,
                       goodput_bytes=a.goodput_bytes, rtt_s=a.rtt_s,
                       budget_mb_per_h=a.budget_mb_per_h)
        print(f"session {rec.sid} recording -> {a.db}  (Ctrl-C to stop)")
        rec.run(a.duration, on_tick=_status)
        n = db.query("SELECT COUNT(*) n, SUM(bytes) b FROM measurements WHERE session_id=?",
                     (rec.sid,))[0]
        print(f"\nsession {rec.sid}: {n['n']} measurements, {(n['b'] or 0)/1e6:.2f} MB used")
        dest = a.export
        shared = Path.home() / "storage" / "shared"
        if dest is None and is_android and shared.exists():
            dest = shared / "Documents" / "trainnet" / "trainnet.sqlite"
        if dest:
            print("copied to", export(db, dest))

    elif a.cmd == "export":
        print("copied to", export(db, a.dest))

    elif a.cmd == "merge":
        for old, new, label in merge(db, a.src):
            print(f"session {old} -> {new}  {label or ''}")
        print("done")

    elif a.cmd == "portal-test":
        url = PORTAL_PRESETS.get(a.url, a.url)
        pos, raw = read_portal(url)
        print(json.dumps(raw, indent=1)[:3000])
        print("\nparsed position:", pos)

    elif a.cmd == "gpx":
        n = 0
        for t, lat, lon, sp in read_gpx(a.file):
            db.add_position(a.session, t, lat, lon, sp, "gpx")
            n += 1
        print(f"{n} GPX points attached to session {a.session}")

    elif a.cmd == "route":
        from . import analyze
        if a.session:
            pts = [(r["lat"], r["lon"]) for r in db.query(
                "SELECT lat, lon FROM positions WHERE session_id=? ORDER BY t", (a.session,))]
        else:
            pts = [(lat, lon) for _, lat, lon, _ in read_gpx(a.gpx)]
        if len(pts) < 2:
            sys.exit("no positions: record with --portal or attach a GPX first")
        r = analyze.save_route(db, a.name, analyze.simplify(pts))
        print(f"route {a.name}: {len(r.pl)} vertices, {r.length/1000:.1f} km")

    elif a.cmd == "towers":
        from . import analyze
        bbox = [float(x) for x in a.bbox.split(",")] if a.bbox else None
        print(analyze.import_towers(db, a.csv, bbox), "towers imported")

    elif a.cmd == "profile":
        from . import analyze
        ids = [int(x) for x in a.sessions.split(",")] if a.sessions else None
        prof = analyze.profile(db, a.route, ids, a.bin, not a.no_weather)
        print(json.dumps({"profile": prof, "correlations": analyze.correlations(prof)},
                         indent=1))

    elif a.cmd == "serve":
        import os
        import uvicorn
        os.environ["TRAINNET_DB"] = str(a.db)
        uvicorn.run("trainnet.api:app", host=a.host, port=a.port)


if __name__ == "__main__":
    main()
