"""SQLite storage. One file holds every trip; long-format measurements keep the API simple."""
import json
import sqlite3
import threading
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "trainnet.sqlite"

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
