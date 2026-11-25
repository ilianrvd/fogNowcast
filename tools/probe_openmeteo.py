"""One-off probe of the Open-Meteo Historical Forecast API (read-only, writes nothing).

Run only from GitHub Actions (see .github/workflows/probe-openmeteo.yml); the
sandbox cannot reach Open-Meteo. For a few points and models it prints, per month,
how many hourly values are present for each variable, so we can see whether
ICON-EU covers the archive period and where the gaps are.
"""
import collections
import json
import sys
import time
import urllib.parse
import urllib.request

URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
UA = "fogNowcast-probe/0.1 (+https://github.com/ilianrvd/fogNowcast)"
VARS = ["temperature_2m", "dew_point_2m", "wind_speed_10m", "wind_direction_10m"]
# name, lat, lon, elevation (m); coordinates from static/points.json and static/ntu_stations.json
POINTS = [
    ("LBSF", 42.696667, 23.411389, 531),
    ("LBBG", 42.569722, 27.515278, 41),
    ("NTU:1 Motel Ihtiman", 42.426981, 23.854053, 700),
]
MODELS = ["icon_eu", "icon_seamless"]
START, END = "2026-01-27", "2026-11-24"
EARLY = ("2025-01-01", "2025-01-07")  # does the model go back further?


def fetch(lat, lon, elev, model, start, end):
    q = {
        "latitude": lat, "longitude": lon, "elevation": elev,
        "start_date": start, "end_date": end,
        "hourly": ",".join(VARS), "models": model,
        "wind_speed_unit": "kn", "timezone": "UTC",
    }
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(q), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code} {e.reason}", "body": e.read()[:300].decode("utf-8", "replace")}


def summarize(data):
    if "error" in data:
        print("  ", data)
        return
    h = data["hourly"]
    times = h["time"]
    print(f"   units: {data.get('hourly_units')}")
    print(f"   elevation used: {data.get('elevation')}, grid point: {data.get('latitude')},{data.get('longitude')}")
    months = collections.OrderedDict()
    for i, t in enumerate(times):
        m = months.setdefault(t[:7], collections.Counter())
        m["hours"] += 1
        for v in VARS:
            if h[v][i] is not None:
                m[v] += 1
    for mon, c in months.items():
        print(f"   {mon}: hours={c['hours']} " + " ".join(f"{v}={c[v]}" for v in VARS))


def main() -> int:
    for name, lat, lon, elev in POINTS:
        for model in MODELS:
            print(f"== {name} / {model} / {START}..{END}")
            summarize(fetch(lat, lon, elev, model, START, END))
            time.sleep(2)
    name, lat, lon, elev = POINTS[0]
    print(f"== earlier period {EARLY[0]}..{EARLY[1]} / icon_eu / {name}")
    summarize(fetch(lat, lon, elev, "icon_eu", *EARLY))
    return 0


if __name__ == "__main__":
    sys.exit(main())
