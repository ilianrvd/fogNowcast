"""Model values by station from the Open-Meteo Historical Forecast API (ICON-EU).

Not available from the dev sandbox; runs in GitHub Actions. Historical Forecast stitches the first
hours of successive runs, so errors are smaller than those of a real 6-12 h forecast
(see docs/correction_model.md).
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
UA = "fogNowcast/0.1 (+https://github.com/ilianrvd/fogNowcast)"
HOURLY = ["temperature_2m", "dew_point_2m", "wind_speed_10m", "wind_direction_10m"]
RETRY_WAIT_S = (5, 15, 45)


def http_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_station(lat, lon, elev_m, start, end, model="icon_eu", fetch=http_json, sleep=time.sleep):
    """{hour_datetime(UTC): {t, td, dd, ws, wd}} for start..end (YYYY-MM-DD, inclusive).
    Retries on network errors; raises after the last attempt (no silent partial data)."""
    q = {"latitude": lat, "longitude": lon, "elevation": elev_m, "start_date": start, "end_date": end,
         "hourly": ",".join(HOURLY), "models": model, "wind_speed_unit": "kn", "timezone": "UTC"}
    url = URL + "?" + urllib.parse.urlencode(q)
    for attempt in range(len(RETRY_WAIT_S) + 1):
        try:
            data = fetch(url)
            break
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == len(RETRY_WAIT_S):
                raise
            sleep(RETRY_WAIT_S[attempt])
    h = data["hourly"]
    out = {}
    for i, ts in enumerate(h["time"]):
        t, td = h["temperature_2m"][i], h["dew_point_2m"][i]
        out[datetime.strptime(ts, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)] = {
            "t": t, "td": td, "dd": t - td if t is not None and td is not None else None,
            "ws": h["wind_speed_10m"][i], "wd": h["wind_direction_10m"][i]}
    return out
