# Advanced: how it works

## What is measured, and why

On a train, the device's Wi-Fi only reaches the access point in the carriage. That link is
short and nearly constant. **Internet quality is set by the train's cellular backhaul (the
LTE/5G router on the roof).** So Wi-Fi RSSI is logged as a control variable, and
connectivity is measured end to end:

| probe     | what                                              | every  | approx. cost |
|-----------|---------------------------------------------------|--------|--------------|
| `wifi`    | RSSI, PHY rate, channel, BSSID (local)            | 2 s    | 0            |
| `tcp_rtt` | TCP handshake to 1.1.1.1, 8.8.8.8, 9.9.9.9 :443   | 2 s    | ~1.1 MB/h    |
| `http204` | `generate_204` → real internet vs. captive portal | 10 s   | ~0.4 MB/h    |
| `dns`     | uncached lookup time                              | 10 s   | ~0.05 MB/h   |
| `goodput` | 100 kB download, throughput after first byte      | 120 s  | ~3 MB/h      |
| `portal`  | onboard portal GPS (train LAN only, optional)     | 5 s    | 0 uplink     |
| `cell`    | Android: serving cell RSRP/RSRQ, cell ID, neighbours | 5 s | 0            |
| `radio`   | Android: LTE/5G, data state, operator             | 30 s   | 0            |

Total is about **5 MB/h**, with a hard cap (default 10 MB/h) that pauses the download test
first. A 100 kB download is a *lower bound* on capacity (TCP slow start): use it to
compare places and trips, not as a speed-test number.

"Online" in the app means: in a test round (every 2 s), at least one of the three servers
completed a TCP handshake. An **outage** is a run of failed rounds lasting at least 4 s.

## Position

- **Android:** GPS through Termux:API, streamed continuously. Distance along the trip is
  the cumulated GPS track; jumps faster than 430 km/h are treated as glitches.
- **Train Wi-Fi portal** (Mac): many trains publish their GPS on the Wi-Fi portal; the
  requests stay on the train's network. The presets (`cd`, `ice`, `oebb`) are
  *unverified*: check on board with `trainnet portal-test cd`, which prints the raw JSON
  and the position found (lat/lon keys are detected by name).
- **GPX** (Mac): record with any GPS logger app on a phone, then
  `trainnet gpx <trip id> track.gpx`. Matched by timestamp.

macOS shows the SSID/BSSID only if the terminal has Location permission.

## Command line

```
trainnet                        the app (default)
trainnet record --label X       record in the terminal without the page (Ctrl-C to stop)
trainnet merge phone.sqlite     import trips from another device; already known trips are skipped
trainnet export file.sqlite     copy the database
trainnet portal-test cd         check a train portal on board
trainnet gpx 3 track.gpx        attach a GPS track to trip 3
trainnet route NAME --session 3 define a line (reference track) from trip 3, or --gpx file
trainnet towers cells.csv       import antenna positions (OpenCelliD CSV), --bbox lat0,lon0,lat1,lon1
trainnet profile NAME           multi-trip 1D profile + correlations, JSON
```

Global options: `--db PATH` (default `~/trainnet-data/trainnet.sqlite`, or `$TRAINNET_DB`),
`--port 8765`, `--no-browser`. `record` options: `--portal`, `--duration`,
`--goodput-every`, `--goodput-bytes`, `--rtt-every`, `--budget` (MB/h).

`route`, `towers` and `profile` need numpy: `pip install '.[analysis]'`.

## Static or dynamic causes (multi-trip analysis)

`trainnet profile NAME` projects all trips onto the line NAME, in bins (default 500 m),
adds distance to the nearest antenna and hourly weather (Open-Meteo, cached), and reports:

- **reproducibility**: Spearman ρ between the bin profiles of every pair of trips. If the
  holes are at the same km every time (ρ close to 1), the cause is infrastructure.
- **drivers**: ρ of per-(trip, bin) metrics against distance to the nearest antenna, the
  phone's cell signal, train speed, precipitation, cloud cover, humidity and hour of day.

Caveat: within one trip, weather changes with position, so it is confounded with
geography. A weather effect only shows up from repeated trips on the same line in
different weather, compared bin by bin.

## Android notes

- Termux and Termux:API must come from the same source (F-Droid).
- A wake lock is taken while recording. If recording still stops in the background on
  Android 12+, the system's "phantom process" limit may be killing it: see the Termux wiki
  for how to disable it with `adb`.
- With Wi-Fi off, the tests run over your own mobile data (about 5 MB/h from your plan) and
  measure the operator's network directly.

## Data API (for a custom frontend)

The app serves JSON on `http://127.0.0.1:8765` (local only). Times are unix seconds (UTC).

| request | returns |
|---|---|
| `GET /api/status` | recording state and live values |
| `POST /api/start` `{"label": "...", "portal": "cd"}` | `{"trip_id": 3}`, or 409 if already recording |
| `POST /api/stop` | `{"trip_id": 3}` |
| `GET /api/trips` | list of trips |
| `GET /api/trips/{id}` | summary, outages, time and km charts |
| `GET /api/trips/{id}/raw` | every measurement and position |

`GET /api/status` while recording:
```json
{"recording": true, "android": true, "portals": ["cd", "ice", "oebb"],
 "trip_id": 3, "label": "Praha → Brno", "elapsed_s": 1260,
 "online": true, "rtt_ms": 42.0, "download_kbps": 8000.0,
 "wifi_dbm": -61, "cell_dbm": -97, "speed_kmh": 98, "has_position": true,
 "gps_fixes": 612, "data_mb_last_hour": 1.9}
```

`GET /api/trips/{id}`:
```json
{"trip": {"id": 3, "label": "Praha → Brno", "started": 1795209000, "ended": 1795210800,
          "platform": "android"},
 "summary": {"duration_s": 1800, "online_pct": 93.9, "outages": 2, "longest_outage_s": 90,
             "rtt_ms_median": 60.5, "download_kbps_median": 8000.0,
             "distance_km": 50.1, "data_used_mb": 2.1},
 "outages": [{"start": 1795209600, "duration_s": 90, "km": 16.7}],
 "timeline": {"step_s": 15, "buckets": [
   {"t": 1795209000, "online": 1.0, "rtt_ms": 55.2, "download_kbps": null,
    "wifi_dbm": -60, "cell_dbm": -95}]},
 "along": {"step_km": 1, "buckets": [
   {"km0": 16, "km1": 17, "online": 0.67, "rtt_ms": 61.0, "download_kbps": null,
    "wifi_dbm": -60, "cell_dbm": -110}]}}
```
`online` in a bucket is the fraction of test rounds that succeeded (0 to 1). `along` is
`null` when the trip has no position.

`GET /api/trips/{id}/raw` returns `{"measurements": [...], "positions": [...]}`. Each
measurement is `{"t", "probe", "target", "ok", "value", "bytes", "extra"}`; `value` is in
dBm (wifi, cell), ms (tcp_rtt, http204, dns) or kbit/s (goodput); `extra` holds
probe-specific details (for `cell`: the serving cell's type, ci, pci, tac, mcc, mnc,
rsrp, rsrq, and the neighbour list).
