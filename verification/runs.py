"""ICON-EU single runs by point from the Open-Meteo Single Runs API (one request per run, all points).

Not reachable from the dev sandbox; runs in GitHub Actions. Requests are retried (TLS handshakes to
Open-Meteo time out now and then).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

URL = "https://single-runs-api.open-meteo.com/v1/forecast"
UA = "fogNowcast/0.1 (+https://github.com/ilianrvd/fogNowcast)"
HOURLY = ["temperature_2m", "dew_point_2m", "wind_speed_10m", "cloud_cover_low", "visibility"]
RETRY_WAIT_S = (5, 15)
RATE_LIMIT_WAIT_S = (60, 180)  # HTTP 429: wait longer (or as long as Retry-After says, up to 300 s)
MAX_LEAD_H = 16  # a run is used for t0 from run+4 h to run+9 h (next run is usable at run+10 h), plus 6 h ahead


def http_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def build_url(run, points, model="icon_eu"):
    q = {"latitude": ",".join(f"{p['lat']:.6f}" for p in points),
         "longitude": ",".join(f"{p['lon']:.6f}" for p in points),
         "elevation": ",".join(str(p["elev_m"]) for p in points),
         "run": run.strftime("%Y-%m-%dT%H:%M"), "hourly": ",".join(HOURLY), "models": model,
         "wind_speed_unit": "kn", "timezone": "UTC"}
    return URL + "?" + urllib.parse.urlencode(q)


def parse(data, points, run):
    """{point_id: {valid_datetime: {t, td, dd, ws, cl}}} for leads 0..MAX_LEAD_H."""
    items = data if isinstance(data, list) else [data]
    if len(items) != len(points):
        raise RuntimeError(f"expected {len(points)} locations, got {len(items)}")
    out = {}
    for p, d in zip(points, items):
        h = d["hourly"]
        series = {}
        for i, ts in enumerate(h["time"]):
            valid = datetime.strptime(ts, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
            lead = (valid - run).total_seconds() / 3600
            if lead < 0 or lead > MAX_LEAD_H:
                continue
            t, td = h["temperature_2m"][i], h["dew_point_2m"][i]
            series[valid] = {"t": t, "td": td, "dd": t - td if t is not None and td is not None else None,
                             "ws": h["wind_speed_10m"][i], "cl": h["cloud_cover_low"][i],
                             "vis": (h.get("visibility") or [None] * len(h["time"]))[i]}
        out[p["id"]] = series
    return out


def fetch_run(run, points, fetch=http_json, sleep=time.sleep):
    url = build_url(run, points)
    n_err = n_429 = 0
    while True:
        try:
            return parse(fetch(url), points, run)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                if n_429 == len(RATE_LIMIT_WAIT_S):
                    raise
                ra = e.headers.get("Retry-After") if e.headers else None
                wait = min(300, int(ra)) if ra and ra.isdigit() else RATE_LIMIT_WAIT_S[n_429]
                n_429 += 1
                sleep(wait)
                continue
            if n_err == len(RETRY_WAIT_S):
                raise
            sleep(RETRY_WAIT_S[n_err])
            n_err += 1
        except (urllib.error.URLError, TimeoutError, OSError):
            if n_err == len(RETRY_WAIT_S):
                raise
            sleep(RETRY_WAIT_S[n_err])
            n_err += 1


def to_json(series_by_point):
    return {pid: {v.strftime("%Y-%m-%dT%H:%M"): vals for v, vals in s.items()} for pid, s in series_by_point.items()}


def from_json(d):
    return {pid: {datetime.strptime(k, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc): vals for k, vals in s.items()}
            for pid, s in d.items()}
