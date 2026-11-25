"""Fetch station elevations from the Open-Meteo Elevation API (Copernicus DEM, 90 m).

Run from GitHub Actions (.github/workflows/elevations-ntu.yml). Reads ntu_stations.json
(produced by ingest.ntu_archive), writes a compact JSON {id: {elev_m, ...}} and prints it.
Cross-check: elevation from the station's mean pressure (ISA) should be close to the DEM value.
"""
import argparse
import json
import sys
import urllib.parse
import urllib.request

API = "https://api.open-meteo.com/v1/elevation"
UA = "fogNowcast-elevation/0.1 (+https://github.com/ilianrvd/fogNowcast)"
MEAN_SEA_LEVEL_HPA = 1016.0   # approximate annual mean MSL pressure over Bulgaria
BATCH = 100
CHECK_TOLERANCE_M = 150


def isa_elevation(p_hpa: float, p0_hpa: float = MEAN_SEA_LEVEL_HPA) -> float:
    return 44330.0 * (1.0 - (p_hpa / p0_hpa) ** 0.1903)


def fetch(lats, lons):
    q = urllib.parse.urlencode({"latitude": ",".join(f"{x:.6f}" for x in lats),
                                "longitude": ",".join(f"{x:.6f}" for x in lons)})
    req = urllib.request.Request(f"{API}?{q}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    el = data["elevation"]
    if len(el) != len(lats):
        raise RuntimeError(f"expected {len(lats)} elevations, got {len(el)}")
    return el


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stations", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    with open(a.stations, encoding="utf-8") as f:
        st = json.load(f)["stations"]
    ids = [k for k, v in st.items() if v.get("lat") is not None and v.get("lon") is not None]
    out = {}
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        el = fetch([st[k]["lat"] for k in chunk], [st[k]["lon"] for k in chunk])
        for k, e in zip(chunk, el):
            out[k] = {"elev_m": round(e), "source": "Open-Meteo Elevation API (Copernicus DEM 90 m)"}
    flagged = []
    for k, v in out.items():
        p = st[k].get("pressure_mean_hpa")
        if p:
            v["elev_from_pressure_m"] = round(isa_elevation(p))
            v["diff_m"] = v["elev_m"] - v["elev_from_pressure_m"]
            if abs(v["diff_m"]) > CHECK_TOLERANCE_M:
                flagged.append(k)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"stations: {len(out)}, checked against pressure: "
          f"{sum('diff_m' in v for v in out.values())}, off by > {CHECK_TOLERANCE_M} m: {len(flagged)}")
    for k in flagged:
        print(f"  CHECK {k} {st[k]['name']}: DEM {out[k]['elev_m']} m, from pressure {out[k]['elev_from_pressure_m']} m")
    print("---BEGIN ELEVATIONS---")
    print(json.dumps({k: [v["elev_m"], v.get("elev_from_pressure_m")] for k, v in out.items()},
                     ensure_ascii=False, separators=(",", ":")))
    print("---END ELEVATIONS---")
    return 0


if __name__ == "__main__":
    sys.exit(main())
