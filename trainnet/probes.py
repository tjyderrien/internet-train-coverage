"""Low-footprint network probes. Each returns a dict for DB.add_measurement.

Typical cost with default intervals (see collect.DEFAULTS):
  tcp_rtt  ~ 3 handshakes x ~200 B every 2 s   ~ 1.1 MB/h
  http204  ~ 1 KB every 10 s                    ~ 0.4 MB/h
  dns      ~ 150 B every 10 s                   ~ 0.05 MB/h
  goodput  100 kB every 120 s                   ~ 3 MB/h
=> roughly 5 MB/h total, much less than one minute of video.
"""
import socket
import time
import uuid
import urllib.request

try:
    import CoreWLAN
except ImportError:  # non-macOS: wifi probe reports not-ok
    CoreWLAN = None

UA = "trainnet/0.1"


def wifi():
    """Laptop <-> onboard access point link. Costs no uplink data."""
    if CoreWLAN is None:
        return dict(probe="wifi", target=None, ok=False)
    iface = CoreWLAN.CWWiFiClient.sharedWiFiClient().interface()
    rssi = iface.rssiValue()
    ch = iface.wlanChannel()
    extra = {
        "noise_dbm": iface.noiseMeasurement(),
        "tx_rate_mbps": iface.transmitRate(),
        "channel": ch.channelNumber() if ch else None,
        "band_ghz": {1: 2.4, 2: 5, 3: 6}.get(ch.channelBand()) if ch else None,
        "ssid": iface.ssid(),     # None unless the terminal has Location permission
        "bssid": iface.bssid(),   # idem; useful to see AP handovers between carriages
    }
    return dict(probe="wifi", target=iface.interfaceName(), ok=rssi != 0,
                value=rssi or None, extra=extra)


def tcp_rtt(host, port=443, timeout=3.0):
    """TCP handshake time to an anycast host. No root needed, ~200 B."""
    t0 = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            ms = (time.perf_counter() - t0) * 1e3
        return dict(probe="tcp_rtt", target=host, ok=True, value=ms, nbytes=200)
    except OSError as e:
        return dict(probe="tcp_rtt", target=host, ok=False, nbytes=120,
                    extra={"err": type(e).__name__})


def http204(url="http://connectivitycheck.gstatic.com/generate_204", timeout=5.0):
    """Is there real internet (not just a captive portal)? Full request time."""
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            code = r.status
            r.read()
        ms = (time.perf_counter() - t0) * 1e3
        return dict(probe="http204", target=url, ok=code == 204, value=ms, nbytes=900,
                    extra={"status": code})
    except Exception as e:
        return dict(probe="http204", target=url, ok=False, nbytes=300,
                    extra={"err": type(e).__name__})


def dns(domain="example.com"):
    """Uncached DNS lookup time (random label defeats caches; NXDOMAIN is the success case)."""
    name = f"{uuid.uuid4().hex[:12]}.{domain}"
    t0 = time.perf_counter()
    try:
        socket.getaddrinfo(name, None)
        ok = True
    except socket.gaierror as e:
        # NXDOMAIN means the resolver answered; anything else = failure
        ok = e.errno in (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", -999))
    ms = (time.perf_counter() - t0) * 1e3
    return dict(probe="dns", target=domain, ok=ok, value=ms if ok else None, nbytes=150)


def goodput(nbytes=100_000, timeout=15.0):
    """Download a small object and measure throughput after the first byte.

    Excluding time-to-first-byte separates capacity from latency. 100 kB is short,
    so on a fast link TCP slow start caps the result: read it as a lower bound and
    compare runs with each other, not with a full speed test.
    """
    url = f"https://speed.cloudflare.com/__down?bytes={nbytes}"
    t0 = time.perf_counter()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            first = r.read(1)
            t1 = time.perf_counter()
            got = len(first)
            while chunk := r.read(16384):
                got += len(chunk)
        t2 = time.perf_counter()
        body_s = max(t2 - t1, 1e-4)
        kbps = (got - 1) * 8 / body_s / 1e3
        return dict(probe="goodput", target="cloudflare", ok=got >= nbytes, value=kbps,
                    nbytes=got + 1500,
                    extra={"ttfb_ms": (t1 - t0) * 1e3, "total_ms": (t2 - t0) * 1e3,
                           "bytes": got})
    except Exception as e:
        return dict(probe="goodput", target="cloudflare", ok=False, nbytes=1500,
                    extra={"err": type(e).__name__})
