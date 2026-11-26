"""Fog onset at the airports: a separate algorithm for the hours without fog (docs/fog_onset.md).

Question: no fog now (METAR at t0 and t0 - 1 h), will there be fog (METAR vis < 1000 m with FG/FZFG) in the
window 1-3 h or 3-6 h after t0? Four methods, the same leave-one-week-out check and the same stop criterion,
fixed before the run:
  climatology (airport x month x 3-hour slot), a simple rule, logistic regression, shallow gradient boosting.
Research only, not for operational use.

Usage: python -m verification.onset --archive-dir DIR --metar-dir DIR --out DIR [--period A:B] [--cache-dir DIR]
"""
import argparse
import glob
import json
import math
import random
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from correction.hourly import hourly_obs, parse_time, sun_elevation_deg

from . import core, events

UTC = timezone.utc
H = timedelta(hours=1)
WINDOWS = {"1-3": (1, 3), "3-6": (3, 6)}
TAUS = {"t": 9, "td": 9, "ws": 6}          # from the stage-2 verification (training October 2026)
STOP = {"warned_2h_share_min": 0.25, "far_max": 0.7}   # plus: 95 % interval of CSI above 0
RULE = {"dd_max": 1.0, "ws_max": 5.0}       # night, deficit <= 1 C, wind <= 5 kt, deficit falling over 3 h
# Dynamic regime (from the fog-model-bulgaria experience, Ilian Manafov 2026-11-26): wind above 4.9 kt or
# BKN/OVC/VV below 5000 ft. Wind: METAR at t0 AND the corrected model mean in the window both above the limit
# (calm now or calm later can still give radiation fog). Ceiling: METAR at t0.
REGIME = {"ws_max_kt": 4.9, "ceil_min_ft": 5000}
SOURCE = ("METAR (Iowa Environmental Mesonet); ICON-EU single runs (Open-Meteo); NTU archive (bgtoll.bg, CC BY 4.0); "
          "fogNowcast docs/fog_onset.md")

FEATURES = ["dd0", "d_dd_1h", "d_dd_3h", "ws0", "d_ws_3h", "vis0", "d_vis_3h", "cover0", "d_cover_3h", "ceil0",
            "night", "cold", "mod_dd_min", "mod_dd_drop", "mod_ws_mean", "mod_cl_mean",
            "ntu_dd_min", "ntu_d_dd_3h", "ntu_missing"]


def _dd(o):
    return core.sat_deficit(o.get("t"), o.get("td"))


def _ceil_index(ft):
    return 1.0 if ft is None else min(ft, 5000) / 5000.0


def _corrected(o0, m0, mL, var, lead):
    v0, w0, wl = o0.get(var), m0.get(var), mL.get(var)
    if None in (v0, w0, wl):
        return None
    v = wl + (v0 - w0) * core.weight(lead, TAUS[var])
    return max(0.0, v) if var == "ws" else v


def window_target(obs, pid, t0, a, b):
    """True/False: fog in any hour of t0+a .. t0+b; None if more than one hour of the window has no METAR."""
    known = [obs.get((pid, t0 + h * H)) for h in range(a, b + 1)]
    known = [o for o in known if o is not None and "fog" in o]
    if len(known) < (b - a + 1) - 1:
        return None
    return any(o["fog"] for o in known)


def ntu_features(obs, station_ids, t0):
    """Smallest saturation deficit of the NTU stations near the airport at t0 and its change over 3 h."""
    now, before = [], []
    for sid in station_ids:
        o0, o3 = obs.get((sid, t0)), obs.get((sid, t0 - 3 * H))
        d0 = _dd(o0) if o0 else None
        if d0 is not None:
            now.append(d0)
            d3 = _dd(o3) if o3 else None
            if d3 is not None:
                before.append(d0 - d3)
    if not now:
        return 0.0, 0.0, 1.0
    return min(now), (min(before) if before else 0.0), 0.0


def samples(obs, model, runs, airports, lat_lon, near, t0s):
    """One sample per (airport, t0, window). Cases are the same for every method: METAR at t0, t0-1, t0-3
    (deficit, wind, visibility), the usable model run, and a known window."""
    out = []
    for t0 in t0s:
        run = core.usable_run(t0, runs)
        if run is None:
            continue
        for pid in airports:
            o0, o1, o3 = obs.get((pid, t0)), obs.get((pid, t0 - H)), obs.get((pid, t0 - 3 * H))
            if not (o0 and o1 and o3) or "fog" not in o0 or "fog" not in o1 or o0["fog"] or o1["fog"]:
                continue
            dd0, dd1, dd3 = _dd(o0), _dd(o1), _dd(o3)
            if None in (dd0, dd1, dd3, o0.get("ws"), o3.get("ws"), o0.get("vis"), o3.get("vis")):
                continue
            series = model.get(run, {}).get(pid, {})
            m0 = series.get(t0)
            if m0 is None:
                continue
            ntu = ntu_features(obs, near.get(pid, []), t0)
            lat, lon = lat_lon[pid]
            for wname, (a, b) in WINDOWS.items():
                y = window_target(obs, pid, t0, a, b)
                if y is None:
                    continue
                dds, wss, cls = [], [], []
                for lead in range(a, b + 1):
                    mL = series.get(t0 + lead * H)
                    if mL is None:
                        continue
                    t = _corrected(o0, m0, mL, "t", lead)
                    td = _corrected(o0, m0, mL, "td", lead)
                    d = core.sat_deficit(t, td)
                    if d is not None:
                        dds.append(d)
                    ws = _corrected(o0, m0, mL, "ws", lead)
                    if ws is not None:
                        wss.append(ws)
                    if mL.get("cl") is not None:
                        cls.append(mL["cl"])
                if not dds or not wss:
                    continue
                mid = t0 + (a + b) / 2 * H
                f = {"dd0": dd0, "d_dd_1h": dd0 - dd1, "d_dd_3h": dd0 - dd3,
                     "ws0": o0["ws"], "d_ws_3h": o0["ws"] - o3["ws"],
                     "vis0": core.vis_index(o0["vis"]), "d_vis_3h": core.vis_index(o0["vis"]) - core.vis_index(o3["vis"]),
                     "cover0": o0.get("low_cover") or 0.0,
                     "d_cover_3h": (o0.get("low_cover") or 0.0) - (o3.get("low_cover") or 0.0),
                     "ceil0": _ceil_index(o0.get("ceil_ft")),
                     "night": 1.0 if sun_elevation_deg(mid, lat, lon) <= 0 else 0.0,
                     "cold": 1.0 if (o0.get("t") is not None and o0["t"] < 0) else 0.0,
                     "mod_dd_min": min(dds), "mod_dd_drop": min(dds) - dd0,
                     "mod_ws_mean": sum(wss) / len(wss), "mod_cl_mean": (sum(cls) / len(cls)) if cls else 0.0,
                     "ntu_dd_min": ntu[0], "ntu_d_dd_3h": ntu[1], "ntu_missing": ntu[2]}
                ceil = o0.get("ceil_ft")
                dyn = (o0["ws"] > REGIME["ws_max_kt"] and f["mod_ws_mean"] > REGIME["ws_max_kt"]) or \
                      (ceil is not None and ceil < REGIME["ceil_min_ft"])
                out.append({"point": pid, "t0": t0, "window": wname, "y": bool(y), "f": f, "dynamic": dyn,
                            "x": [f[k] for k in FEATURES]})
    return out


# ---- methods ----

def rule_decision(s):
    f = s["f"]
    return f["night"] == 1.0 and f["dd0"] <= RULE["dd_max"] and f["ws0"] <= RULE["ws_max"] and f["d_dd_3h"] < 0


def _slot(s):
    return (s["point"], s["t0"].month, s["t0"].hour // 3)


def fit_climatology(train):
    base = sum(s["y"] for s in train) / len(train)
    cnt = defaultdict(lambda: [0, 0])
    for s in train:
        c = cnt[_slot(s)]
        c[0] += s["y"]
        c[1] += 1
    a = 20.0  # shrink small slots towards the overall frequency
    return lambda s: (cnt[_slot(s)][0] + a * base) / (cnt[_slot(s)][1] + a) if _slot(s) in cnt else base


def _standardize(train_x):
    n, k = len(train_x), len(train_x[0])
    mu = [sum(x[j] for x in train_x) / n for j in range(k)]
    sd = [math.sqrt(sum((x[j] - mu[j]) ** 2 for x in train_x) / n) or 1.0 for j in range(k)]
    return lambda x: [1.0] + [(x[j] - mu[j]) / sd[j] for j in range(k)]


def _newton(X, y, lam=1.0, iters=30):
    """Ridge logistic regression by Newton's method (numpy); the intercept (column 0) is not penalised and
    starts at the log-odds of the base rate, so rare events get calibrated probabilities."""
    import numpy as np
    A, b = np.asarray(X, float), np.asarray(y, float)
    base = min(max(b.mean(), 1e-6), 1 - 1e-6)
    w = np.zeros(A.shape[1])
    w[0] = math.log(base / (1 - base))
    pen = np.full(A.shape[1], lam)
    pen[0] = 0.0
    def loss(v):
        z = np.clip(A @ v, -30, 30)
        return float(np.sum(np.logaddexp(0, z) - b * z) + 0.5 * np.sum(pen * v * v))

    cur = loss(w)
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(A @ w, -30, 30)))
        g = A.T @ (p - b) + pen * w
        Hm = (A * (p * (1 - p))[:, None]).T @ A + np.diag(pen) + 1e-6 * np.eye(A.shape[1])
        step = np.linalg.solve(Hm, g)
        t = 1.0
        while t > 1e-4:              # damped Newton: the loss must go down
            new = loss(w - t * step)
            if new <= cur:
                break
            t /= 2
        else:
            break
        w, done = w - t * step, cur - new < 1e-8 * max(1.0, abs(cur))
        cur = new
        if done:
            break
    return [float(v) for v in w]


def fit_logistic(train):
    z = _standardize([s["x"] for s in train])
    X, y = [z(s["x"]) for s in train], [1.0 if s["y"] else 0.0 for s in train]
    try:
        w = _newton(X, y)
    except ImportError:          # without numpy: plain gradient descent (slower, less well converged)
        w = core.fit_logistic(X, y)
    return lambda s: core.predict(w, z(s["x"])), w


def fit_boosting(train):
    """Shallow gradient boosting (scikit-learn); None when scikit-learn is not installed."""
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
    except ImportError:
        return None
    m = HistGradientBoostingClassifier(max_depth=3, max_iter=150, learning_rate=0.05, min_samples_leaf=40,
                                       l2_regularization=1.0, random_state=1)
    m.fit([s["x"] for s in train], [int(s["y"]) for s in train])
    return lambda batch: [float(p) for p in m.predict_proba([s["x"] for s in batch])[:, 1]]


THRESHOLDS = [t / 100 for t in range(1, 60)]


def _csi(dec, ys):
    tp = sum(1 for d, y in zip(dec, ys) if d and y)
    fp = sum(1 for d, y in zip(dec, ys) if d and not y)
    fn = sum(1 for d, y in zip(dec, ys) if not d and y)
    return tp / (tp + fp + fn) if tp + fp + fn else 0.0


def best_threshold(ps, ys):
    return max(THRESHOLDS, key=lambda t: _csi([p >= t for p in ps], ys))


def _predict_batch(method, train, test):
    """Probabilities for test from a model fitted on train; None if the method is unavailable.
    *_calm: fitted on the calm regime only; a sample in the dynamic regime gets probability 0."""
    if method.endswith("_calm"):
        tr = [s for s in train if not s["dynamic"]]
        calm = [i for i, s in enumerate(test) if not s["dynamic"]]
        out = [0.0] * len(test)
        if not calm or not any(s["y"] for s in tr):
            return out
        p = _predict_batch(method[:-5], tr, [test[i] for i in calm])
        if p is None:
            return None
        for i, v in zip(calm, p):
            out[i] = v
        return out
    if method == "climatology":
        f = fit_climatology(train)
        return [f(s) for s in test]
    if method == "logistic":
        f, _ = fit_logistic(train)
        return [f(s) for s in test]
    if method == "boosting":
        f = fit_boosting(train)
        return None if f is None else f(test)
    raise ValueError(method)


def cv_predict(rows, method, inner_folds=3):
    """Leave-one-week-out. The decision threshold is chosen on out-of-fold predictions inside the training
    weeks (inner_folds groups of weeks), so it is never tuned on the predicted week nor on in-sample fits."""
    week = [s["t0"].isocalendar()[:2] for s in rows]
    weeks = sorted(set(week))
    out = [None] * len(rows)
    for wk in weeks:
        tr_i = [i for i, w in enumerate(week) if w != wk]
        te_i = [i for i, w in enumerate(week) if w == wk]
        tr = [rows[i] for i in tr_i]
        if not any(s["y"] for s in tr):
            continue
        tr_weeks = sorted({week[i] for i in tr_i})
        inner_p, inner_y = [], []
        for g in range(inner_folds):
            hold = {w for j, w in enumerate(tr_weeks) if j % inner_folds == g}
            a = [s for i, s in zip(tr_i, tr) if week[i] not in hold]
            b = [s for i, s in zip(tr_i, tr) if week[i] in hold]
            if not b or not any(s["y"] for s in a):
                continue
            pb = _predict_batch(method, a, b)
            if pb is None:
                return None
            inner_p += pb
            inner_y += [s["y"] for s in b]
        thr = best_threshold(inner_p, inner_y) if inner_p else 0.5
        pt = _predict_batch(method, tr, [rows[i] for i in te_i])
        if pt is None:
            return None
        for i, p in zip(te_i, pt):
            out[i] = (p, p >= thr)
    return out


# ---- scores ----

def bootstrap_csi(days, dec, ref, ys, n=500, seed=1):
    """95 % intervals by day of CSI(dec) and of CSI(dec) - CSI(ref)."""
    agg = defaultdict(lambda: [0] * 6)
    for d, a, r, y in zip(days, dec, ref, ys):
        g = agg[d]
        g[0] += a and y; g[1] += a and not y; g[2] += (not a) and y
        g[3] += r and y; g[4] += r and not y; g[5] += (not r) and y
    keys = sorted(agg)
    if len(keys) < 3:
        return None, None
    rnd = random.Random(seed)
    c1s, ds = [], []
    for _ in range(n):
        s = [0] * 6
        for k in (rnd.choice(keys) for _ in keys):
            s = [x + y for x, y in zip(s, agg[k])]
        c1 = s[0] / (s[0] + s[1] + s[2]) if s[0] + s[1] + s[2] else 0.0
        c0 = s[3] / (s[3] + s[4] + s[5]) if s[3] + s[4] + s[5] else 0.0
        c1s.append(c1); ds.append(c1 - c0)
    c1s.sort(); ds.sort()
    lo, hi = int(0.025 * n), int(0.975 * n) - 1
    return [round(c1s[lo], 3), round(c1s[hi], 3)], [round(ds[lo], 3), round(ds[hi], 3)]


def episode_warnings(rows, decisions, eps, window):
    """Per episode onset T: the longest lead T - t0 of a positive decision from a clear t0 whose window covers T."""
    a, b = WINDOWS[window]
    by_pt = defaultdict(list)
    for s, d in zip(rows, decisions):
        if d:
            by_pt[s["point"]].append(s["t0"])
    leads = []
    for pt, lst in eps.items():
        for onset, _end in lst:
            best = 0
            for t0 in by_pt.get(pt, []):
                h = (onset - t0).total_seconds() / 3600
                if a <= h <= b:
                    best = max(best, int(round(h)))
            leads.append(best)
    n = len(leads)
    return {"episodes": n, "warned_2h": sum(1 for x in leads if x >= 2),
            "warned_2h_share": round(sum(1 for x in leads if x >= 2) / n, 3) if n else None,
            "warned_any": sum(1 for x in leads if x >= 1), "missed": sum(1 for x in leads if x == 0)}


def score(rows, preds, rule_dec, eps, window, with_prob=True):
    idx = [i for i, p in enumerate(preds) if p is not None]
    sel = [rows[i] for i in idx]
    ys = [s["y"] for s in sel]
    dec = [preds[i][1] for i in idx]
    ps = [preds[i][0] for i in idx]
    c = core.contingency([1.0 if d else 0.0 for d in dec], [1.0 if y else 0.0 for y in ys], 0.5)
    ci, ci_rule = bootstrap_csi([s["t0"].date() for s in sel], dec, [rule_dec[i] for i in idx], ys)
    out = {"n": len(sel), "events": sum(ys), "pod": c["pod"], "far": c["far"], "csi": c["csi"],
           "csi_ci95": ci, "csi_minus_rule_ci95": ci_rule,
           "episodes": episode_warnings(sel, dec, eps, window)}
    if with_prob:
        out["brier"] = round(core.brier(ps, [1.0 if y else 0.0 for y in ys]), 5)
        out["mean_p"] = round(sum(ps) / len(ps), 4) if ps else None
        out["obs_freq"] = round(sum(ys) / len(ys), 4) if ys else None
    far = out["far"] if out["far"] is not None else 1.0
    share = out["episodes"]["warned_2h_share"] or 0.0
    out["passes"] = bool(share >= STOP["warned_2h_share_min"] and far < STOP["far_max"] and ci and ci[0] > 0)
    return out


def run_all(rows_all, eps):
    res = {}
    for wname in WINDOWS:
        rows = [s for s in rows_all if s["window"] == wname]
        rule_dec = [rule_decision(s) for s in rows]
        w = {"rows": len(rows), "events": sum(s["y"] for s in rows),
             "dynamic_rows": sum(s["dynamic"] for s in rows),
             "dynamic_events": sum(1 for s in rows if s["dynamic"] and s["y"])}
        w["rule"] = score(rows, [(1.0 if d else 0.0, d) for d in rule_dec], rule_dec, eps, wname, with_prob=False)
        for m in ("climatology", "logistic", "boosting", "logistic_calm", "boosting_calm"):
            p = cv_predict(rows, m)
            w[m] = {"note": "scikit-learn not installed"} if p is None else score(rows, p, rule_dec, eps, wname)
        if rows and any(s["y"] for s in rows):
            _, wts = fit_logistic(rows)   # all data: only to read which predictors carry the signal
            w["logistic_weights"] = dict(sorted(zip(["bias"] + FEATURES, [round(v, 3) for v in wts]),
                                                key=lambda kv: -abs(kv[1])))
        res[wname] = w
    return res


METHOD_BG = {"rule": "правило", "climatology": "климатология", "logistic": "логистична регресия",
             "boosting": "gradient boosting", "logistic_calm": "регресия, само спокоен режим",
             "boosting_calm": "boosting, само спокоен режим"}
METHODS = ("rule", "climatology", "logistic", "boosting", "logistic_calm", "boosting_calm")
LEARNED = ("logistic", "boosting", "logistic_calm", "boosting_calm")


def report(res):
    L = ["# Поява на мъгла на летищата (изследване, не за оперативна употреба)", "",
         f"Период: {res['period'][0]} … {res['period'][1]}. Само часове без мъгла в t0 и t0 − 1 ч. "
         "Цел: мъгла по METAR (видимост < 1000 m с FG/FZFG) в прозореца след t0. Проверка по седмици: всяка седмица "
         "е предсказана от модел, обучен на останалите; прагът е избран вътре в обучаващите седмици.", "",
         f"Стоп-условие (записано преди пускането): поне {STOP['warned_2h_share_min']:.0%} от епизодите предупредени "
         f"поне 2 ч по-рано, FAR под {STOP['far_max']} и 95 % интервал на CSI над 0. "
         f"Епизоди: {res['episodes']} (начало след поне 2 ч без мъгла).", ""]
    for wname, w in res["windows"].items():
        L += [f"## Прозорец {wname} ч ({w['rows']} случая, {w['events']} с мъгла)", "",
              f"Динамичен режим (вятър > {REGIME['ws_max_kt']} kt сега и в модела, или таван < {REGIME['ceil_min_ft']} ft): "
              f"{w.get('dynamic_rows')} случая, от тях {w.get('dynamic_events')} с мъгла в прозореца "
              "(тях методите „само спокоен режим“ изпускат по построение).", "",
              "| метод | POD | FAR | CSI | ИД CSI | ИД CSI − правило | Brier | ср. p / честота | епизоди ≥ 2 ч | изпуснати | минава |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for m in METHODS:
            r = w.get(m, {})
            if "csi" not in r:
                L.append(f"| {METHOD_BG[m]} | {r.get('note', '—')} |||||||||| ")
                continue
            e = r["episodes"]
            L.append(f"| {METHOD_BG[m]} | {r['pod']} | {r['far']} | {r['csi']} | {r['csi_ci95']} | "
                     f"{r['csi_minus_rule_ci95'] if m != 'rule' else '—'} | {r.get('brier', '—')} | "
                     f"{r.get('mean_p', '—')} / {r.get('obs_freq', '—')} | {e['warned_2h']} ({e['warned_2h_share']}) | "
                     f"{e['missed']} | {'да' if r['passes'] else 'не'} |")
        if w.get("logistic_weights"):
            top = list(w["logistic_weights"].items())[:8]
            L += ["", "Най-тежки предиктори в регресията (стандартизирани, на всички данни): "
                  + ", ".join(f"`{k}` {v}" for k, v in top) + "."]
        L.append("")
    verdict = [f"{METHOD_BG[m]} ({wn} ч)" for wn, w in res["windows"].items()
               for m in LEARNED if w.get(m, {}).get("passes")]
    L += ["## Извод", "", ("Минават стоп-условието: " + ", ".join(verdict) + ".") if verdict else
          "Никой от научените методи не минава стоп-условието.", ""]
    return "\n".join(L) + "\n"


def main(argv=None):
    from .build import hours, load_runs, metar_hourly   # same data loading as the stage-2 verification
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--metar-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--period", default="2026-04-05:2026-11-24")
    ap.add_argument("--cache-dir", default=None)
    a = ap.parse_args(argv)
    p0, p1 = [parse_time(x + "T00:00:00Z") for x in a.period.split(":")]
    end = p1 + timedelta(hours=23)
    airports = [p for p in json.loads(Path("static/points.json").read_text(encoding="utf-8"))["points"]
                if p["type"] == "airport"]
    stations = json.loads(Path("static/correction_stations.json").read_text(encoding="utf-8"))["stations"]
    near = defaultdict(list)
    for s in stations:
        for g in s.get("groups", []):
            if g.startswith("airport:"):
                near[g.split(":", 1)[1]].append(s["id"])
    lat_lon = {p["id"]: (p["lat"], p["lon"]) for p in airports}
    obs = hourly_obs(sorted(glob.glob(str(Path(a.archive_dir) / "ntu_*.csv.gz"))), [s["id"] for s in stations])
    for p in airports:
        f = Path(a.metar_dir) / f"metar_{p['id']}.csv.gz"
        if f.exists():
            obs.update(metar_hourly(f, p["id"]))
    print(f"observations: {len(obs)}", flush=True)
    run_times, r = [], (p0 - timedelta(hours=12)).replace(hour=0)
    while r <= end:
        run_times.append(r)
        r += timedelta(hours=6)
    pts = [{"id": p["id"], "lat": p["lat"], "lon": p["lon"], "elev_m": p["elev_m"]} for p in airports]
    model, runs, failed = load_runs(run_times, pts, str(Path(a.cache_dir) / "airports") if a.cache_dir else None)
    print(f"runs: {len(runs)} ok, {len(failed)} failed", flush=True)
    rows = samples(obs, model, runs, [p["id"] for p in airports], lat_lon, near, hours(p0, end))
    print(f"samples: {len(rows)}", flush=True)
    fog_series = defaultdict(list)
    for (pid, t), o in obs.items():
        if pid in lat_lon and "fog" in o and p0 <= t <= end:
            fog_series[pid].append((t, o["fog"]))
    eps = events.episodes(fog_series)
    res = {"generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": SOURCE,
           "run": "ICON-EU single runs 00/06/12/18 UTC", "status": "research, not for operational use",
           "period": a.period.split(":"), "windows_h": WINDOWS, "stop": STOP, "rule": RULE, "regime": REGIME, "taus_h": TAUS,
           "features": FEATURES, "runs_failed": failed, "episodes": sum(len(v) for v in eps.values()),
           "windows": run_all(rows, eps)}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "onset.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    md = report(res)
    (out / "onset_report.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
