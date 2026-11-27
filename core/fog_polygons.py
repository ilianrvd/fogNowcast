"""Fog-condition polygons 0-6 h: MultiConvect ICON-EU grid + observation residuals spread by distance and height
and decaying with lead (docs/fog_polygons.md). Uncalibrated: polygons of the saturation deficit, not a fog
probability. Needs numpy and contourpy (Actions only).

Usage: python -m core.fog_polygons --npz fog_fields_icon_eu.npz --meta fog_fields_meta.json --ntu ntu_latest.json
                                   [--metar metar_latest.json] --out fog_polygons.geojson [--manifest manifest.json]
"""
import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

from . import fog_fields as ff

SCHEMA = "fognowcast.fog_polygons/1.0"
SOURCE = "fogNowcast core (research, uncalibrated)"
CADENCE_MIN = 15
HORIZON_H = 6
CLASSES = [("fog_conditions", 0.5), ("near_saturation", 1.0)]   # deficit <= 0.5 C and 0.5 .. 1.0 C (Ilian Manafov 2026-11-27)
TAU_H = {"t": 9.0, "td": 9.0, "ws": 6.0}
LAPSE_K_PER_M = {"t": -0.0065, "td": -0.0018, "ws": 0.0}
SPREAD_KM = 30.0                 # Gaussian length scale of a station's influence
SPREAD_DZ_M = 200.0              # ... and of the height difference station - grid cell
BACKGROUND_W = 0.1               # weight of "no correction": far from stations the residual fades to 0
MAX_OBS_AGE_MIN = 90
MAX_RESIDUAL = {"t": 8.0, "td": 8.0, "ws": 15.0}
DYNAMIC = {"ws_kt": 4.9, "clcl_pct": 62.5}  # wind above 4.9 kt or BKN low cloud: no fog conditions
EARTH_KM = 6371.0


def parse_time(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sat_deficit(t, td):
    """Vectorised verification.core.sat_deficit: t - td at t >= 0 C, t - frost point at t < 0 C."""
    import numpy as np
    td = np.minimum(td, t)
    a = np.log(6.112 * np.exp(17.62 * td / (243.12 + td)) / 6.112)
    tf = 272.62 * a / (22.46 - a)
    return np.maximum(0.0, t - np.where(t < 0, tf, td))


def observations(ntu, metar, airports, now):
    """[{id, lat, lon, elev, time, t, td, ws}] fresh and not excluded by QC."""
    out = []
    for s in (ntu or {}).get("stations", []):
        if s.get("qc", {}).get("excluded") or not s.get("time") or s.get("elev_m") is None:
            continue
        out.append({"id": s["id"], "lat": s["lat"], "lon": s["lon"], "elev": float(s["elev_m"]),
                    "time": parse_time(s["time"]), "t": s.get("t"), "td": s.get("td"), "ws": s.get("wind_kt")})
    latest = {}
    for r in (metar or {}).get("reports", []):
        if r.get("id") in airports and r.get("time"):
            tm = parse_time(r["time"])
            if r["id"] not in latest or tm > latest[r["id"]]["time"]:
                a = airports[r["id"]]
                latest[r["id"]] = {"id": r["id"], "lat": a["lat"], "lon": a["lon"], "elev": float(a["elev_m"]),
                                   "time": tm, "t": r.get("t"), "td": r.get("td"), "ws": r.get("wind_kt")}
    out += latest.values()
    return [o for o in out if (now - o["time"]).total_seconds() / 60.0 <= MAX_OBS_AGE_MIN]


def _field_at(fields, var, k, lat, lon):
    if var == "ws":
        u = ff.bilinear(fields["u10"][k], fields["lat"], fields["lon"], lat, lon)
        v = ff.bilinear(fields["v10"][k], fields["lat"], fields["lon"], lat, lon)
        return math.hypot(u, v)
    return ff.bilinear(fields["t2m" if var == "t" else "td2m"][k], fields["lat"], fields["lon"], lat, lon)


def model_at(fields, var, when, lat, lon):
    """Model value at a point and time (linear in time between the two hours); NaN if outside."""
    times = fields["times"]
    lo = when.replace(minute=0, second=0, microsecond=0)
    if lo not in times:
        return float("nan")
    k = times.index(lo)
    a = _field_at(fields, var, k, lat, lon)
    w = (when - lo).total_seconds() / 3600.0
    if w == 0 or k + 1 >= len(times):
        return a if w == 0 else float("nan")
    return a * (1 - w) + _field_at(fields, var, k + 1, lat, lon) * w


def residuals(fields, obs):
    """Per station and variable: obs - model, the model first brought to the station height (lapse rate)."""
    hs = fields["hsurf"]
    out = []
    for o in obs:
        zm = ff.bilinear(hs, fields["lat"], fields["lon"], o["lat"], o["lon"])
        if math.isnan(zm):
            continue
        r = {}
        for var in TAU_H:
            if o.get(var) is None:
                continue
            m = model_at(fields, var, o["time"], o["lat"], o["lon"])
            if math.isnan(m):
                continue
            m += LAPSE_K_PER_M[var] * (o["elev"] - zm)
            if var == "ws":
                m = max(m, 0.0)
            d = o[var] - m
            if abs(d) <= MAX_RESIDUAL[var]:
                r[var] = d
        if r:
            out.append(dict(o, res=r))
    return out


def _weights(lat2d, lon2d, z2d, st):
    import numpy as np
    la, lo = np.radians(lat2d), np.radians(lon2d)
    sla, slo = math.radians(st["lat"]), math.radians(st["lon"])
    x = (lo - slo) * np.cos((la + sla) / 2)
    d = EARTH_KM * np.sqrt(x * x + (la - sla) ** 2)
    return np.exp(-(d / SPREAD_KM) ** 2) * np.exp(-((z2d - st["elev"]) / SPREAD_DZ_M) ** 2)


def spread(fields, stations, var, now, lat2d=None, lon2d=None, z2d=None):
    """Residual field of var at now (each station's residual first decayed by its own age)."""
    import numpy as np
    if lat2d is None:
        lon2d, lat2d = np.meshgrid(fields["lon"], fields["lat"])
        z2d = fields["hsurf"]
    num = np.zeros(lat2d.shape)
    den = np.full(lat2d.shape, BACKGROUND_W)
    for st in stations:
        if var not in st["res"]:
            continue
        age = max(0.0, (now - st["time"]).total_seconds() / 3600.0)
        w = _weights(lat2d, lon2d, z2d, st)
        num += w * st["res"][var] * math.exp(-age / TAU_H[var])
        den += w
    return num / den


def leave_one_out(fields, stations, now):
    """At t0, each station's residual predicted from the others: MAE of the corrected vs the plain model."""
    import numpy as np
    out = {}
    for var in ("t", "td"):
        e_model, e_corr = [], []
        for i, st in enumerate(stations):
            if var not in st["res"]:
                continue
            others = stations[:i] + stations[i + 1:]
            pt = (np.array([[st["lat"]]]), np.array([[st["lon"]]]), np.array([[st["elev"]]]))
            pred = float(spread(fields, others, var, now, *pt)[0, 0])
            e_model.append(abs(st["res"][var]))
            e_corr.append(abs(st["res"][var] - pred))
        n = len(e_model)
        out[var] = {"n": n, "mae_model": round(sum(e_model) / n, 2) if n else None,
                    "mae_corrected": round(sum(e_corr) / n, 2) if n else None}
    return out


def corrected_fields(fields, stations, now, t0):
    """{lead: {"deficit", "dynamic"}} on the model grid."""
    import numpy as np
    res = {v: spread(fields, stations, v, now) for v in TAU_H}
    out = {}
    for lead in range(HORIZON_H + 1):
        valid = t0 + timedelta(hours=lead)
        if valid not in fields["times"]:
            continue
        k = fields["times"].index(valid)
        h = (valid - now).total_seconds() / 3600.0
        dec = {v: math.exp(-max(h, 0.0) / TAU_H[v]) for v in TAU_H}
        t = fields["t2m"][k] + res["t"] * dec["t"]
        td = np.minimum(fields["td2m"][k] + res["td"] * dec["td"], t)
        ws = np.maximum(0.0, np.hypot(fields["u10"][k], fields["v10"][k]) + res["ws"] * dec["ws"])
        dyn = (ws > DYNAMIC["ws_kt"]) | (fields["clcl"][k] >= DYNAMIC["clcl_pct"])
        out[lead] = {"valid": valid, "deficit": sat_deficit(t, td), "dynamic": dyn}
    return out


def polygons(lat, lon, deficit, dynamic):
    """GeoJSON features per class: filled contours of the deficit in the calm regime."""
    import numpy as np
    from contourpy import FillType, contour_generator
    z = np.where(dynamic | np.isnan(deficit), 99.0, deficit)
    gen = contour_generator(x=lon, y=lat, z=z, fill_type=FillType.OuterOffset)
    feats, lower = [], -1.0
    for name, upper in CLASSES:
        points, offsets = gen.filled(lower, upper)
        polys = []
        for pts, off in zip(points, offsets):
            rings = [[[round(float(x), 4), round(float(y), 4)] for x, y in pts[off[j]:off[j + 1]]]
                     for j in range(len(off) - 1)]
            rings = [r for r in rings if len(r) >= 4]
            if rings:
                polys.append(rings)
        if polys:
            feats.append({"type": "Feature", "properties": {"class": name, "deficit_max": upper},
                          "geometry": {"type": "MultiPolygon", "coordinates": polys}})
        lower = upper
    return feats


def build(fields, ntu, metar, airports, now=None):
    now = now or datetime.now(timezone.utc)
    t0 = now.replace(minute=0, second=0, microsecond=0)
    obs = observations(ntu, metar, airports, now)
    stations = residuals(fields, obs)
    cf = corrected_fields(fields, stations, now, t0)
    features = []
    for lead, c in cf.items():
        for f in polygons(fields["lat"], fields["lon"], c["deficit"], c["dynamic"]):
            f["properties"].update({"lead_h": lead, "valid": iso(c["valid"])})
            features.append(f)
    run = fields["run"]
    return {"type": "FeatureCollection", "schema": SCHEMA, "generated_at": iso(now), "source": SOURCE,
            "run": {"icon_eu": run}, "cadence_min": CADENCE_MIN, "t0": iso(t0), "calibrated": False,
            "classes": [{"class": n, "deficit_max_c": v} for n, v in CLASSES],
            "dynamic_regime": DYNAMIC, "spread": {"km": SPREAD_KM, "dz_m": SPREAD_DZ_M, "background_w": BACKGROUND_W},
            "tau_h": TAU_H, "leads_h": sorted(cf),
            "inputs": {"model_age_min": round((now - parse_time(run)).total_seconds() / 60),
                       "stations_used": len(stations)},
            "check": {"leave_one_out_t0": leave_one_out(fields, stations, now)},
            "features": features}


def update_manifest(path, doc):
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return False
    entry = {"id": "fog_polygons", "path": "fog/fog_polygons.geojson", "format": "geojson", "schema": SCHEMA,
             "doc": "https://github.com/ilianrvd/fogNowcast/blob/main/docs/fog_polygons.md",
             "cadence_min": CADENCE_MIN, "generated_at": doc["generated_at"], "data_time": doc["t0"]}
    m["products"] = [p for p in m.get("products", []) if p.get("id") != "fog_polygons"] + [entry]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(m, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return True


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--npz", required=True)
    ap.add_argument("--meta", required=True)
    ap.add_argument("--ntu", required=True)
    ap.add_argument("--metar")
    ap.add_argument("--points", default="static/points.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--manifest")
    a = ap.parse_args(argv)
    load = lambda p: json.load(open(p, encoding="utf-8"))
    airports = {p["id"]: p for p in load(a.points)["points"] if p.get("type") == "airport"}
    doc = build(ff.load(a.npz, a.meta), load(a.ntu), load(a.metar) if a.metar else None, airports)
    tmp = a.out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, a.out)
    if a.manifest:
        update_manifest(a.manifest, doc)
    print(f"fog_polygons: {len(doc['features'])} features, {doc['inputs']['stations_used']} stations, "
          f"leave-one-out {doc['check']['leave_one_out_t0']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
