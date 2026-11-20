"""SQLite storage. One file holds every trip; long-format measurements keep the API simple."""
import json
import os
import sqlite3
import threading
from pathlib import Path

# One database for all trips, in the home folder so it survives reinstalls and updates.
DEFAULT_DB = Path(os.environ.get("TRAINNET_DB", Path.home() / "trainnet-data" / "trainnet.sqlite"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY,
    started REAL NOT NULL,          -- unix seconds UTC
    ended REAL,
    label TEXT,                     -- e.g. "Praha hl.n. -> Brno, R 875"
    host TEXT,
    config TEXT                     -- JSON of the probe settings used
);
-- probe: wifi | tcp_rtt | http204 | dns | goodput | portal
-- value: rssi dBm (wifi), ms (tcp_rtt, http204, dns), kbit/s (goodput)
CREATE TABLE IF NOT EXISTS measurements (
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    t REAL NOT NULL,
    probe TEXT NOT NULL,
    target TEXT,
    ok INTEGER NOT NULL,
    value REAL,
    bytes INTEGER DEFAULT 0,        -- payload bytes moved over the train's uplink
    extra TEXT                      -- JSON, probe specific
);
CREATE INDEX IF NOT EXISTS m_sess_t ON measurements(session_id, t);
CREATE TABLE IF NOT EXISTS positions (
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    t REAL NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    speed_mps REAL,
    source TEXT,                    -- portal | gpx | manual
    extra TEXT
);
CREATE INDEX IF NOT EXISTS p_sess_t ON positions(session_id, t);
CREATE TABLE IF NOT EXISTS routes (
    name TEXT PRIMARY KEY,
    polyline TEXT NOT NULL,         -- JSON [[lat, lon], ...] in travel order
    length_m REAL
);
CREATE TABLE IF NOT EXISTS towers (
    radio TEXT, mcc INTEGER, net INTEGER, area INTEGER, cell INTEGER,
    lat REAL NOT NULL, lon REAL NOT NULL, range_m REAL
);
CREATE INDEX IF NOT EXISTS towers_ll ON towers(lat, lon);
CREATE TABLE IF NOT EXISTS weather (
    lat_r REAL, lon_r REAL, hour INTEGER,   -- 0.1 deg grid, unix hour
    data TEXT,
    PRIMARY KEY (lat_r, lon_r, hour)
);
"""


class DB:
    def __init__(self, path=DEFAULT_DB):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.lock = threading.Lock()

    def execute(self, sql, args=()):
        with self.lock:
            cur = self.conn.execute(sql, args)
            self.conn.commit()
            return cur

    def query(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def add_measurement(self, sid, t, probe, target, ok, value=None, nbytes=0, extra=None):
        self.execute(
            "INSERT INTO measurements VALUES (?,?,?,?,?,?,?,?)",
            (sid, t, probe, target, int(bool(ok)), value, nbytes,
             json.dumps(extra) if extra else None))

    def add_position(self, sid, t, lat, lon, speed=None, source="portal", extra=None):
        self.execute(
            "INSERT INTO positions VALUES (?,?,?,?,?,?,?)",
            (sid, t, lat, lon, speed, source, json.dumps(extra) if extra else None))


def merge(dst, src_path):
    """Copy all sessions from another trainnet DB (e.g. the phone's) into dst, renumbering ids.
    Sessions already present (same start time and host) are skipped."""
    src = DB(src_path)
    done = []
    for s in src.query("SELECT * FROM sessions ORDER BY id"):
        if dst.query("SELECT 1 FROM sessions WHERE started=? AND host IS ?", (s["started"], s["host"])):
            continue
        new = dst.execute("INSERT INTO sessions(started,ended,label,host,config) VALUES (?,?,?,?,?)",
                          (s["started"], s["ended"], s["label"], s["host"], s["config"])).lastrowid
        for table, n in (("measurements", 8), ("positions", 7)):
            rows = src.conn.execute(f"SELECT * FROM {table} WHERE session_id=?", (s["id"],)).fetchall()
            with dst.lock:
                dst.conn.executemany(f"INSERT INTO {table} VALUES ({','.join('?' * n)})",
                                     [(new, *tuple(r)[1:]) for r in rows])
                dst.conn.commit()
        done.append((s["id"], new, s["label"]))
    return done


def export(db, dest):
    """Consistent copy of the live DB (safe while recording), e.g. to Android shared storage."""
    dest = Path(dest).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    out = sqlite3.connect(dest)
    with db.lock:
        db.conn.backup(out)
    out.close()
    return dest
