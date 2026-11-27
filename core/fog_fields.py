"""Reader of the MultiConvect ICON-EU grid for fogNowcast (format: docs/fog_fields.md in MultiConvect,
docs/input_multiconvect.md here). Read in Actions only, needs numpy.

Usage (check of a published file): python -m core.fog_fields --npz fog_fields_icon_eu.npz --meta fog_fields_meta.json
                                    [--points latest_points.json]
"""
import argparse
import json
import math
import sys
from datetime import datetime, timezone

SCHEMA_MAJOR = "multiconvect.fog_fields/1."
VARS = ("t2m", "td2m", "u10", "v10", "clcl")


class FormatError(ValueError):
    pass


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def decode(raw, spec):
    """int array -> float array with NaN where raw == fill."""
    import numpy as np
    a = raw.astype("float64") * float(spec.get("scale", 1.0)) + float(spec.get("offset", 0.0))
    a[raw == int(spec.get("fill", -32768))] = np.nan
    return a


def load(npz_path, meta_path):
    """{"meta", "run", "lat", "lon", "times", "hsurf", vars...} with decoded float arrays; FormatError on mismatch."""
    import numpy as np
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    if not str(meta.get("schema", "")).startswith(SCHEMA_MAJOR):
        raise FormatError(f"schema {meta.get('schema')!r}, expected {SCHEMA_MAJOR}x")
    z = np.load(npz_path)
    lat, lon = z["lat"].astype("float64"), z["lon"].astype("float64")
    grid = meta.get("grid", {})
    if np.any(np.diff(lat) <= 0) or np.any(np.diff(lon) <= 0):
        raise FormatError("lat/lon not ascending")
    if (grid.get("ny"), grid.get("nx")) != (len(lat), len(lon)):
        raise FormatError(f"grid {grid.get('ny')}x{grid.get('nx')} in meta, {len(lat)}x{len(lon)} in npz")
    times = [datetime.fromtimestamp(int(t), tz=timezone.utc) for t in z["valid_time"]]
    out = {"meta": meta, "run": meta.get("run"), "lat": lat, "lon": lon, "times": times}
    specs = meta.get("vars", {})
    for v in ("hsurf",) + VARS:
        if v not in z.files:
            raise FormatError(f"missing array {v}")
        a = decode(z[v], specs.get(v, {}))
        want = (len(lat), len(lon)) if v == "hsurf" else (len(times), len(lat), len(lon))
        if a.shape != want:
            raise FormatError(f"{v} shape {a.shape}, expected {want}")
        out[v] = a
    return out


def bilinear(field2d, lat_axis, lon_axis, lat, lon):
    """Bilinear value at (lat, lon) on an ascending grid; NaN outside the grid or next to a missing value."""
    import numpy as np
    if not (lat_axis[0] <= lat <= lat_axis[-1] and lon_axis[0] <= lon <= lon_axis[-1]):
        return float("nan")
    i = min(int(np.searchsorted(lat_axis, lat) - 1), len(lat_axis) - 2)
    j = min(int(np.searchsorted(lon_axis, lon) - 1), len(lon_axis) - 2)
    i, j = max(i, 0), max(j, 0)
    fy = (lat - lat_axis[i]) / (lat_axis[i + 1] - lat_axis[i])
    fx = (lon - lon_axis[j]) / (lon_axis[j + 1] - lon_axis[j])
    q = field2d[i:i + 2, j:j + 2]
    return float(q[0, 0] * (1 - fy) * (1 - fx) + q[0, 1] * (1 - fy) * fx + q[1, 0] * fy * (1 - fx) + q[1, 1] * fy * fx)


def wind_kt(u, v):
    return math.hypot(u, v)


def check(fields, points=None):
    """Summary of a loaded file and, with latest_points.json, the same run and the grid vs the point values."""
    import numpy as np
    lat, lon, times = fields["lat"], fields["lon"], fields["times"]
    rep = {"run": fields["run"], "ny": len(lat), "nx": len(lon), "hours": len(times),
           "first_valid": _iso(times[0]), "last_valid": _iso(times[-1]),
           "lat": [round(float(lat[0]), 4), round(float(lat[-1]), 4)],
           "lon": [round(float(lon[0]), 4), round(float(lon[-1]), 4)],
           "missing_share": {v: round(float(np.isnan(fields[v]).mean()), 4) for v in ("hsurf",) + VARS},
           "ranges": {v: [round(float(np.nanmin(fields[v])), 2), round(float(np.nanmax(fields[v])), 2)]
                      for v in ("hsurf",) + VARS}}
    if points:
        rep["points_run"] = points.get("run")
        rep["same_run"] = points.get("run") == fields["run"]
        diffs = {"t": [], "td": [], "wind_kt": []}
        tix = {t: k for k, t in enumerate(times)}
        for p in points.get("points", []):
            if p.get("lat") is None or p.get("lon") is None:
                continue
            for r in p.get("series", []):
                k = tix.get(datetime.strptime(r["valid"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc))
                if k is None:
                    continue
                g = {"t": bilinear(fields["t2m"][k], lat, lon, p["lat"], p["lon"]),
                     "td": bilinear(fields["td2m"][k], lat, lon, p["lat"], p["lon"])}
                g["wind_kt"] = wind_kt(bilinear(fields["u10"][k], lat, lon, p["lat"], p["lon"]),
                                       bilinear(fields["v10"][k], lat, lon, p["lat"], p["lon"]))
                for var in diffs:
                    if r.get(var) is not None and not math.isnan(g[var]):
                        diffs[var].append(abs(g[var] - r[var]))
        rep["grid_vs_points_mae"] = {k: (round(sum(v) / len(v), 3) if v else None) for k, v in diffs.items()}
        rep["grid_vs_points_n"] = {k: len(v) for k, v in diffs.items()}
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--points", help="latest_points.json of the same MultiConvect run")
    ap.add_argument("--static-points", default="static/points.json")
    ap.add_argument("--stations", default="static/correction_stations.json")
    a = ap.parse_args(argv)
    fields = load(a.npz, a.meta)
    points = None
    if a.points:
        with open(a.points, encoding="utf-8") as f:
            points = json.load(f)
        pos = {}
        for path, key in ((a.static_points, "points"), (a.stations, "stations")):
            with open(path, encoding="utf-8") as f:
                for p in json.load(f)[key]:
                    pos[p["id"]] = (p["lat"], p["lon"])
        for p in points.get("points", []):
            if p["id"] in pos:
                p["lat"], p["lon"] = pos[p["id"]]
    print(json.dumps(check(fields, points), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
