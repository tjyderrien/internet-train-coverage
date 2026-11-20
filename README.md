# trainnet: internet quality along train trips

Records Wi-Fi and internet quality on a train as a function of time and position. Each
trip is turned into a **1D map along the track (km)**, so trips can be compared and the
causes separated: static ones (masts, tunnels, terrain) versus dynamic ones (weather,
crowding, time of day).

## What is measured, and why

On a train, the laptop's Wi-Fi only reaches the access point in the carriage. That link
is short and nearly constant. **Internet quality is set by the train's cellular backhaul
(the LTE/5G router on the roof).** So Wi-Fi RSSI is logged as a control variable, and
connectivity is measured end to end:

| probe     | what                                              | default every | approx. cost |
|-----------|---------------------------------------------------|---------------|--------------|
| `wifi`    | RSSI, noise, PHY rate, channel (CoreWLAN, local)  | 2 s           | 0            |
| `tcp_rtt` | TCP handshake to 1.1.1.1, 8.8.8.8, 9.9.9.9 :443   | 2 s           | ~1.1 MB/h    |
| `http204` | `generate_204` → real internet vs. captive portal | 10 s          | ~0.4 MB/h    |
| `dns`     | uncached lookup time                              | 10 s          | ~0.05 MB/h   |
| `goodput` | 100 kB download, throughput after first byte      | 120 s         | ~3 MB/h      |
| `portal`  | onboard portal GPS (train LAN only)               | 5 s           | 0 uplink     |

Total is about **5 MB/h**, with a hard cap (`--budget`, default 10 MB/h) that pauses the
download test first. A 100 kB download is a *lower bound* on capacity (TCP slow start).
Use it to compare places and trips, not as a speed-test number.

## Position

1. **Onboard portal** (`--portal cd|ice|oebb|URL`). Many trains publish GPS on their
   Wi-Fi portal. The presets are *unverified*: check on board first with
   `trainnet portal-test cd`, which prints the raw JSON and the position it found
   (lat/lon keys are detected by name).
2. **Phone GPX** (most reliable). Run any GPS logger app on the phone during the trip,
   then `trainnet gpx <session> track.gpx`. Measurements are matched to positions by
   timestamp, so both clocks must be on network time (the default).

macOS shows the SSID/BSSID only if the terminal has Location permission
(System Settings → Privacy → Location Services). The BSSID shows the laptop switching
between access points in different carriages.

## Usage

```bash
cd ~/train-connectivity && source .venv/bin/activate
trainnet record --label "Praha → Brno R 875" --portal cd     # Ctrl-C at arrival
trainnet gpx 3 ~/Downloads/track.gpx                         # optional
trainnet route praha-brno --session 3                        # once per line
trainnet towers cell_towers.csv --bbox 48.5,12,51.1,19       # OpenCelliD export, once
trainnet profile praha-brno --bin 500                        # JSON to stdout
trainnet serve                                               # API at http://127.0.0.1:8765/docs
```

Everything is stored in `data/trainnet.sqlite`.

## Android phone (Termux)

The phone records everything itself: GPS, train Wi-Fi, the same internet probes, **and its
own cellular signal** (serving cell RSRP/RSRQ, cell ID, neighbours, LTE/5G). Analysis
stays on the Mac.

What the phone's cell signal adds: the train router and your phone see roughly the same
outside coverage. If the internet through the train Wi-Fi drops where the phone still has a
good cell, the train's backhaul is the problem. If both drop, it is coverage.

**Two modes:**
- *Phone on train Wi-Fi* (normal): the probes measure the train internet, and cell info
  records the outside coverage at the same time.
- *Wi-Fi off*: the probes run over your own mobile data (about 5 MB/h from your plan),
  which measures the operator's network directly.

### One-time setup
1. Install **Termux** and **Termux:API** from F-Droid. Both must come from the same
   source; mixing F-Droid and Play Store builds fails. **Termux:Widget** is optional and
   gives a home-screen button.
2. Android settings → Apps → Termux:API → Permissions: Location **"Allow all the time"**,
   Phone **Allow**. Settings → Battery: set Termux and Termux:API to **Unrestricted**.
3. Copy `dist/trainnet-android.tar.gz` to the phone, for example over home Wi-Fi: on the Mac
   run `cd ~/train-connectivity/dist && python3 -m http.server 8000`, then in Termux:
   ```sh
   pkg install -y curl
   curl -O http://<mac-ip>:8000/trainnet-android.tar.gz   # Mac IP: ipconfig getifaddr en1
   tar xzf trainnet-android.tar.gz && sh trainnet/android/install-termux.sh
   ```

### On the train
```sh
trainnet record --label "Praha -> Brno"            # Ctrl-C (Volume-Down + C) on arrival
```
The screen can be off: a wake lock is taken automatically. At the end, the database is
copied to `Documents/trainnet/trainnet.sqlite` on the phone's shared storage.

### Back on the Mac
```sh
trainnet merge ~/Downloads/trainnet.sqlite         # imports new trips, skips known ones
```

If recording stops in the background on Android 12+, the system's "phantom process"
limit may be killing it. See the Termux wiki for how to disable it with `adb`.

## Analysis: static or dynamic?

`/routes/{name}/correlations` returns:

* **reproducibility**: Spearman ρ between the bin profiles of every pair of trips. If the
  holes are at the same km every time (ρ close to 1), the cause is infrastructure.
* **drivers**: ρ of per-(trip, bin) metrics against distance to the nearest tower, train
  speed, precipitation, cloud cover, humidity and hour of day (weather: Open-Meteo,
  hourly, cached).

Caveat: within one trip, weather changes with position, so it is confounded with
geography. A weather effect only shows up from repeated trips on the same line in
different weather, compared bin by bin.

---

## API contract for the frontend (Claude Design)

Base URL `http://127.0.0.1:8765`, JSON, CORS open, read-only. The full schema is at
`/openapi.json`. Times are unix seconds (UTC), distances in metres.

### `GET /sessions` → list of trips
```json
[{"id":1,"label":"Praha → Brno","started":1795168222.1,"ended":1795175422.3,
  "host":"Mac","n_measurements":9700,"n_positions":1400,"bytes_used":10322350}]
```

### `GET /live?window_s=60` → live dashboard while recording
```json
{"session":{...},"recording":true,
 "summary":{"n":97,"online":1.0,"rtt_ms_median":13.1,"rtt_ms_p90":27.0,"loss":0.0,
            "dns_ms_median":24.9,"goodput_kbps_median":44473.9,"goodput_n":1,
            "wifi_rssi_median":-58.0,"cell_dbm_median":-97.0},
 "position":{"t":...,"lat":50.08,"lon":14.43,"speed_mps":27.1},
 "measurements":[ /* same rows as timeseries */ ]}
```

### `GET /sessions/{id}/timeseries?probe=tcp_rtt&since=<t>` → time domain
```json
[{"t":1795168224.5,"probe":"tcp_rtt","target":"1.1.1.1","ok":1,"value":13.2,"bytes":200,"extra":null},
 {"t":1795168224.6,"probe":"wifi","target":"en1","ok":1,"value":-57,"bytes":0,
  "extra":{"noise_dbm":-96,"tx_rate_mbps":576,"channel":36,"band_ghz":5,"ssid":null,"bssid":null}},
 {"t":1795168230.1,"probe":"goodput","target":"cloudflare","ok":1,"value":44473.9,
  "extra":{"ttfb_ms":31.0,"total_ms":49.0,"bytes":100000}}]
```
Android adds `cell` (value = serving-cell RSRP in dBm; `extra.serving` holds type,
ci, pci, tac, mcc, mnc, rsrq, ...; `extra.neighbours` is a list) and `radio` (`extra`:
data_network_type, data_state, operator). Phone positions have `source: "gps"`.
`value` units: wifi = dBm, tcp_rtt/http204/dns = ms, goodput = kbit/s. `ok=0` means a
failure (the most important signal: dead zones).

### `GET /sessions/{id}/positions` → `[{"t","lat","lon","speed_mps","source"}]`

### `GET /routes`, `GET /routes/{name}` → `{"name","length_m","polyline":[[lat,lon],...]}`

### `GET /routes/{name}/profile?sessions=1,2&bin_m=500&weather=true` → the 1D map
```json
{"route":"praha-brno","length_m":255000,"bin_m":500,"sessions":[1,2],
 "bins":[{"bin":0,"s0_m":0,"s1_m":500,"lat":50.083,"lon":14.435,
          "tower_nearest_m":640,"towers_2km":7,
          "all":{ /* summary over all trips, same keys as live.summary */ },
          "trips":{"1":{ /* summary */, "t_mean":1795168230,"speed_kmh":82.4,
                        "weather":{"precipitation":0.2,"rain":0,"snowfall":0,
                                   "cloud_cover":100,"relative_humidity_2m":91,
                                   "temperature_2m":4.1,"wind_speed_10m":12}}}}]}
```
A bin with no data has `"all": null`.

### `GET /routes/{name}/correlations?sessions=&bin_m=500`
```json
{"reproducibility":{"online":{"mean_rho":0.82,"pairs":[{"a":"1","b":"2","rho":0.82,"n_bins":480}]}, ...},
 "drivers":{"rtt_ms_median":{"tower_nearest_m":{"rho":0.41,"n":950},"precipitation":{...},...}, ...}}
```

### `GET /towers?lat0&lon0&lat1&lon1` → `[{"radio","mcc","net","lat","lon","range_m"}]`

### Suggested views
1. **Live**: big "online" indicator, RTT sparkline, current km and speed, MB used.
2. **Trip timeline**: RTT/goodput/RSSI against time, with failures shaded.
3. **1D strip map**: the track km on x, one row per trip with a colour-coded metric,
   a tower-density row and a precipitation row. Stacked rows make it obvious whether
   dead zones repeat.
4. **Map**: route polyline coloured by the selected metric, plus towers.
5. **Drivers**: a ρ heatmap (metric × driver) and the reproducibility score.
