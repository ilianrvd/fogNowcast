"""Cells of systematic model error (median) by station x regime, lookup with fallback, evaluation."""
import statistics
from collections import defaultdict

from .hourly import sun_elevation_deg

MIN_N = 30            # a cell with fewer pairs gets no correction
CALM_KT = 5.0         # observed wind below this is "calm"
SAT_RH = 95.0         # observed rh at or above this is "sat"
MIN_WIND_FOR_DIR = 3.0
VARS = ("t", "td", "dd", "ws", "wd")


def ang_diff(a, b):
    """Signed smallest angle a - b in degrees, in (-180, 180]."""
    d = (a - b) % 360
    return d - 360 if d > 180 else d


def regime(obs, hour, lat, lon):
    """Regime from the OBSERVATION: (day|night, calm|windy|None, sat|dry|None)."""
    dn = "day" if sun_elevation_deg(hour, lat, lon) > 0 else "night"
    ws, rh = obs.get("ws"), obs.get("rh")
    wind = None if ws is None else ("calm" if ws < CALM_KT else "windy")
    sat = None if rh is None else ("sat" if rh >= SAT_RH else "dry")
    return dn, wind, sat


def error(var, mod, obs):
    """model - observation; None when either side is missing or wind direction is not meaningful."""
    m, o = mod.get(var), obs.get(var)
    if m is None or o is None:
        return None
    if var == "wd":
        if obs.get("ws") is None or obs["ws"] < MIN_WIND_FOR_DIR:
            return None
        return ang_diff(m, o)
    return m - o


def build_pairs(obs_by_key, model_by_station, stations):
    """List of pairs {station, time, obs, mod, regime} for hours where both sides exist."""
    pairs = []
    for (sid, hour), obs in obs_by_key.items():
        mod = model_by_station.get(sid, {}).get(hour)
        if mod is None:
            continue
        st = stations[sid]
        pairs.append({"station": sid, "time": hour, "obs": obs, "mod": mod,
                      "regime": regime(obs, hour, st["lat"], st["lon"])})
    return pairs


def _keys(reg):
    dn, wind, sat = reg
    yield "full", f"{dn}|{wind}|{sat}" if wind and sat else None
    yield "sat", sat
    yield "station", "all"


def fit_cells(pairs):
    """{station: {var: {level: {key: {"median": x, "n": n}}}}}, only cells with n >= MIN_N."""
    groups = defaultdict(list)
    for p in pairs:
        for var in VARS:
            e = error(var, p["mod"], p["obs"])
            if e is None:
                continue
            for level, key in _keys(p["regime"]):
                if key is not None:
                    groups[(p["station"], var, level, key)].append(e)
    cells = {}
    for (sid, var, level, key), errs in groups.items():
        if len(errs) >= MIN_N:
            cells.setdefault(sid, {}).setdefault(var, {}).setdefault(level, {})[key] = {
                "median": round(statistics.median(errs), 3), "n": len(errs)}
    return cells


def lookup(cells, sid, var, reg):
    """(level, median) from the finest cell that exists, or None."""
    by_level = cells.get(sid, {}).get(var, {})
    for level, key in _keys(reg):
        if key is not None and key in by_level.get(level, {}):
            return level, by_level[level][key]["median"]
    return None


def corrected(var, mod_value, median):
    v = mod_value - median
    if var == "wd":
        return v % 360
    if var == "ws":
        return max(0.0, v)
    return v


def _abs_err(var, value, obs_value):
    return abs(ang_diff(value, obs_value)) if var == "wd" else abs(value - obs_value)


def evaluate(pairs, cells, min_n=MIN_N):
    """Per station and variable: errors before and after the correction on `pairs`.
    Pairs without a usable cell stay uncorrected (coverage shows how many were corrected)."""
    acc = defaultdict(lambda: {"n": 0, "corr": 0, "ae0": 0.0, "ae1": 0.0, "b0": 0.0, "b1": 0.0,
                               "w1_0": 0, "w1_1": 0, "fog": {"n": 0, "ae0": 0.0, "ae1": 0.0}})
    for p in pairs:
        for var in VARS:
            e0 = error(var, p["mod"], p["obs"])
            if e0 is None:
                continue
            a = acc[(p["station"], var)]
            hit = lookup(cells, p["station"], var, p["regime"])
            e1 = e0
            if hit:
                a["corr"] += 1
                if var == "wd":
                    e1 = ang_diff(corrected(var, p["mod"][var], hit[1]), p["obs"][var])
                else:
                    e1 = corrected(var, p["mod"][var], hit[1]) - p["obs"][var]
            a["n"] += 1
            a["ae0"] += abs(e0)
            a["ae1"] += abs(e1)
            a["b0"] += e0
            a["b1"] += e1
            a["w1_0"] += abs(e0) < 1
            a["w1_1"] += abs(e1) < 1
            dn, wind, sat = p["regime"]
            if sat == "sat" and wind == "calm":
                a["fog"]["n"] += 1
                a["fog"]["ae0"] += abs(e0)
                a["fog"]["ae1"] += abs(e1)
    out = {}
    for (sid, var), a in acc.items():
        n = a["n"]
        r = {"n": n, "coverage": round(a["corr"] / n, 3),
             "mae_before": round(a["ae0"] / n, 3), "mae_after": round(a["ae1"] / n, 3),
             "bias_before": round(a["b0"] / n, 3), "bias_after": round(a["b1"] / n, 3),
             "within1_before": round(a["w1_0"] / n, 3), "within1_after": round(a["w1_1"] / n, 3)}
        f = a["fog"]
        r["sat_calm"] = {"n": f["n"],
                         "mae_before": round(f["ae0"] / f["n"], 3) if f["n"] else None,
                         "mae_after": round(f["ae1"] / f["n"], 3) if f["n"] else None}
        r["accepted"] = n >= min_n and r["mae_after"] < r["mae_before"]
        out.setdefault(sid, {})[var] = r
    return out
