"""One-off probe of the Open-Meteo Single Runs API for ICON-EU (read-only, writes nothing).

Run only from GitHub Actions (.github/workflows/probe-single-runs.yml). For a few runs at LBSF it prints
how many hourly values come back, the first and last valid time and whether temperature/dew point are
present, so we know whether real lead-time verification is possible before our own archive grows.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

URL = "https://single-runs-api.open-meteo.com/v1/forecast"
UA = "fogNowcast-probe/0.1 (+https://github.com/ilianrvd/fogNowcast)"
VARS = ["temperature_2m", "dew_point_2m", "wind_speed_10m", "wind_direction_10m", "cloud_cover_low", "visibility"]
RUNS = ["2026-04-03T00:00", "2026-10-15T00:00", "2026-11-20T06:00"]
MODELS = ["icon_eu"]


def fetch(model, run):
    q = {"latitude": 42.696667, "longitude": 23.411389, "elevation": 531, "run": run, "hourly": ",".join(VARS),
         "models": model, "wind_speed_unit": "kn", "timezone": "UTC"}
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(q), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "body": e.read()[:300].decode("utf-8", "replace")}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def main() -> int:
    for model in MODELS:
        for run in RUNS:
            d = fetch(model, run)
            if "error" in d:
                print(f"{model} {run}: {d}")
            else:
                h = d["hourly"]
                t = h["time"]
                present = {v: sum(x is not None for x in h.get(v, [])) for v in VARS}
                print(f"{model} {run}: hours={len(t)} first={t[0] if t else '-'} last={t[-1] if t else '-'} "
                      f"present={present} grid={d.get('latitude')},{d.get('longitude')}")
                if t:
                    vis = h.get("visibility") or []
                    print(f"   first 3 t: {h['temperature_2m'][:3]} td: {h['dew_point_2m'][:3]}")
                    print(f"   visibility units: {d.get('hourly_units', {}).get('visibility')}; first 12: {vis[:12]}")
                    print(f"   visibility min/max: {min((v for v in vis if v is not None), default=None)} / "
                          f"{max((v for v in vis if v is not None), default=None)}; distinct: {len(set(vis))}")
            time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
