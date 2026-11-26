"""Uncalibrated corrected point forecast: ICON-EU (MultiConvect) + decaying residual from observations.

corrected(valid) = model(valid) + (obs(t_obs) - model(t_obs)) * exp(-(valid - t_obs) / tau)

No fog probability yet: the saturation deficit is published as is. Calibration comes from verification/.
Usage: python -m core.fog_points --model latest_points.json --ntu ntu_latest.json [--metar metar_latest.json]
                               --points static/points.json --out fog_points.json
"""
import argparse
import json
import math
from datetime import datetime, timedelta, timezone

from verification.core import sat_deficit

TAU_H = {"t": 9.0, "td": 9.0, "wind_kt": 6.0}   # from verification (training Oct 2026), see docs/verification.md
HORIZON_H = 6
MAX_OBS_AGE_MIN = 90      # older observation: point is given by the model only
SOURCE = "fogNowcast core v1 (research, uncalibrated)"


def parse_time(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def model_at(series, when, var):
    """Linear interpolation in time between the two hourly model values; None if either is missing."""
    by_time = {parse_time(r["valid"]): r for r in series}
    lo = when.replace(minute=0, second=0, microsecond=0)
    a, b = by_time.get(lo), by_time.get(lo + timedelta(hours=1))
    if a is None or a.get(var) is None:
        return None
    if lo == when:
        return a[var]
    if b is None or b.get(var) is None:
        return None
    w = (when - lo).total_seconds() / 3600.0
    return a[var] * (1 - w) + b[var] * w


def weight(age_h, tau):
    return math.exp(-max(age_h, 0.0) / tau)


def pick_obs(point_id, ntu, metar, now):
    """Latest usable observation of the point: dict(time, t, td, wind_kt, source) or None."""
    if point_id.startswith("NTU:"):
        for s in ntu.get("stations", []):
            if s["id"] == point_id and not s.get("qc", {}).get("excluded"):
                return {"time": parse_time(s["time"]), "t": s.get("t"), "td": s.get("td"),
                        "wind_kt": s.get("wind_kt"), "source": "ntu"}
        return None
    best = None
    for r in (metar or {}).get("reports", []):
        if r.get("id") == point_id and r.get("time"):
            tm = parse_time(r["time"])
            if best is None or tm > best["time"]:
                best = {"time": tm, "t": r.get("t"), "td": r.get("td"), "wind_kt": r.get("wind_kt"), "source": "metar"}
    return best


def build_point(meta, model_point, obs, t0, now):
    series = model_point["series"]
    residual = {}
    if obs is not None and (now - obs["time"]).total_seconds() / 60.0 > MAX_OBS_AGE_MIN:
        obs = None
    if obs is not None:
        for var in TAU_H:
            m0 = model_at(series, obs["time"], var)
            if obs.get(var) is not None and m0 is not None:
                residual[var] = obs[var] - m0
    by_time = {parse_time(r["valid"]): r for r in series}
    out = []
    for lead in range(HORIZON_H + 1):
        valid = t0 + timedelta(hours=lead)
        row = by_time.get(valid)
        if row is None:
            continue
        vals, weights = {}, []
        for var, tau in TAU_H.items():
            v = row.get(var)
            if v is not None and var in residual:
                w = weight((valid - obs["time"]).total_seconds() / 3600.0, tau)
                v = v + residual[var] * w
                weights.append(w)
            vals[var] = None if v is None else round(v, 1)
        if vals["wind_kt"] is not None:
            vals["wind_kt"] = max(0, round(vals["wind_kt"]))
        if vals["td"] is not None and vals["t"] is not None:
            vals["td"] = min(vals["td"], vals["t"])
        dd = sat_deficit(vals["t"], vals["td"])
        out.append({"valid": iso(valid), "lead_h": lead, "t": vals["t"], "td": vals["td"],
                    "deficit": None if dd is None else round(dd, 1),
                    "wind_kt": vals["wind_kt"], "wind_dir": row.get("wind_dir"),
                    "cloud_low_pct": row.get("cloud_low_pct"),
                    "corr_weight": round(100 * (sum(weights) / len(weights))) if weights else 0})
    return {"id": meta["id"], "name": meta.get("name"), "lat": meta["lat"], "lon": meta["lon"],
            "elev_m": meta.get("elev_m"), "model_elev_m": model_point.get("model_elev_m"),
            "obs_source": obs["source"] if obs else None,
            "obs_time": iso(obs["time"]) if obs else None,
            "residual": {k: round(v, 1) for k, v in residual.items()} if residual else None,
            "series": out}


def build(model, ntu, metar, points, now=None):
    now = now or datetime.now(timezone.utc)
    t0 = now.replace(minute=0, second=0, microsecond=0)
    meta = {p["id"]: p for p in points["points"]}
    result = []
    for mp in model["points"]:
        if mp["id"] not in meta:
            continue
        obs = pick_obs(mp["id"], ntu, metar, now)
        result.append(build_point(meta[mp["id"]], mp, obs, t0, now))
    inputs = {"model_age_min": round((now - parse_time(model["run"])).total_seconds() / 60)}
    if ntu.get("obs_time_latest"):
        inputs["ntu_age_min"] = round((now - parse_time(ntu["obs_time_latest"])).total_seconds() / 60)
    if metar and metar.get("fetched_at"):
        inputs["metar_age_min"] = round((now - parse_time(metar["fetched_at"])).total_seconds() / 60)
    return {"schema": "fog_points/1.0", "generated_at": iso(now), "source": SOURCE,
            "run": {"icon_eu": model["run"]}, "t0": iso(t0), "calibrated": False,
            "units": {"t": "degC", "td": "degC", "deficit": "degC", "wind_kt": "kt", "wind_dir": "deg",
                      "cloud_low_pct": "%", "corr_weight": "%"},
            "inputs": inputs, "tau_h": TAU_H, "points": result}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--ntu", required=True)
    ap.add_argument("--metar")
    ap.add_argument("--points", default="static/points.json")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    load = lambda p: json.load(open(p, encoding="utf-8"))
    doc = build(load(a.model), load(a.ntu), load(a.metar) if a.metar else None, load(a.points))
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, separators=(",", ":"))
    print(f"fog_points: {len(doc['points'])} points, {sum(1 for p in doc['points'] if p['obs_source'])} with observation")


if __name__ == "__main__":
    main()
