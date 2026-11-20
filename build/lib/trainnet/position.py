"""Position sources.

1. Onboard portal: many trains publish GPS on their local Wi-Fi portal. Requests stay on
   the train's LAN (no cellular data). The JSON layout differs per operator, so lat/lon are
   found by key name. Run `trainnet portal-test URL` on board to check a URL.
2. GPX from a phone GPS logger, merged afterwards by timestamp (most reliable option).
"""
import json
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime

# Candidate endpoints, NOT verified by us. Check each on board with `trainnet portal-test`.
PORTAL_PRESETS = {
    "cd": "http://cdwifi.cz/portal/api/vehicle/realtime",       # Ceske drahy
    "ice": "https://iceportal.de/api1/rs/status",                # DB ICE
    "oebb": "https://railnet.oebb.at/api/gps",                   # OBB Railjet
}

LAT_KEYS = ("lat", "latitude", "gpslat")
LON_KEYS = ("lon", "lng", "long", "longitude", "gpslng", "gpslon")
SPEED_KEYS = ("speed", "velocity", "gpsspeed")


def _find(obj, keys):
    """Depth-first search for the first numeric value whose key matches."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in keys:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        for v in obj.values():
            r = _find(v, keys)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find(v, keys)
            if r is not None:
                return r
    return None


def read_portal(url, timeout=3.0):
    req = urllib.request.Request(url, headers={"User-Agent": "trainnet/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read())
    lat, lon = _find(raw, LAT_KEYS), _find(raw, LON_KEYS)
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None, raw
    speed = _find(raw, SPEED_KEYS)
    # Portals mostly give km/h; anything above 120 "m/s" would be > 430 km/h.
    speed_mps = speed / 3.6 if speed is not None else None
    return dict(lat=lat, lon=lon, speed_mps=speed_mps), raw


def read_gpx(path):
    """Yield (unix_t, lat, lon, speed_mps|None) from a GPX track."""
    ns = {"g": "http://www.topografix.com/GPX/1/1"}
    root = ET.parse(path).getroot()
    pts = root.findall(".//g:trkpt", ns) or root.findall(".//trkpt")
    for p in pts:
        tel = p.find("g:time", ns) if p.find("g:time", ns) is not None else p.find("time")
        if tel is None:
            continue
        t = datetime.fromisoformat(tel.text.replace("Z", "+00:00")).timestamp()
        sp = p.find(".//{*}speed")
        yield t, float(p.get("lat")), float(p.get("lon")), (
            float(sp.text) if sp is not None else None)
