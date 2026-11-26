"""Pure functions of the stage-2 verification (docs/verification.md, docs/fog_map.md)."""
import math
import random
from collections import defaultdict
from datetime import timedelta

from correction.hourly import sun_elevation_deg

LEADS = range(0, 7)
VARS = ("t", "td", "dd", "ws")
LATENCY_H = 4          # a run is usable this many hours after its start (ICON-EU, all of f000-f030)
TAU_GRID = (1, 2, 3, 4, 6, 9, 12, None)   # None = no decay (residual kept for the whole 6 h)
RUN_HOURS = (0, 6, 12, 18)


def usable_run(t0, runs):
    """Latest run with run + LATENCY_H <= t0, or None."""
    ok = [r for r in runs if r + timedelta(hours=LATENCY_H) <= t0]
    return max(ok) if ok else None


def weight(lead, tau):
    return 1.0 if tau is None else math.exp(-lead / tau)


def build_cases(t0s, obs, model, runs, point_ids):
    """Cases {point, t0, run, lead, var, obs0, mod0, modL, truth, fog0, fogL, night, ws_mod, cl, dd_mod}
    for every t0 with an observation at t0, the usable run, and a truth observation at t0 + lead."""
    cases = []
    for t0 in t0s:
        run = usable_run(t0, runs)
        if run is None:
            continue
        for pid in point_ids:
            o0 = obs.get((pid, t0))
            series = model.get(run, {}).get(pid, {})
            m0 = series.get(t0)
            if o0 is None or m0 is None:
                continue
            for lead in LEADS:
                tl = t0 + timedelta(hours=lead)
                oL, mL = obs.get((pid, tl)), series.get(tl)
                if oL is None or mL is None:
                    continue
                cases.append({"point": pid, "t0": t0, "run": run, "lead": lead, "o0": o0, "m0": m0,
                              "mL": mL, "oL": oL})
    return cases


def forecast(case, var, method, tau=3):
    o0, m0, mL = case["o0"].get(var), case["m0"].get(var), case["mL"].get(var)
    if method == "model":
        return mL
    if method == "persistence":
        return o0
    if None in (o0, m0, mL):
        return None
    v = mL + (o0 - m0) * weight(case["lead"], tau)
    return max(0.0, v) if var in ("ws", "dd") else v


def abs_errors(cases, var, method, tau=3):
    """{lead: [(day, |error|)]}"""
    out = defaultdict(list)
    for c in cases:
        f, truth = forecast(c, var, method, tau), c["oL"].get(var)
        if f is None or truth is None:
            continue
        out[c["lead"]].append((c["t0"].date(), abs(f - truth)))
    return out


def mae(pairs):
    return sum(e for _, e in pairs) / len(pairs) if pairs else None


def fit_tau(cases, var):
    """tau from TAU_GRID with the smallest MAE over leads 1-6 (the lead-0 error is the same for all)."""
    best = None
    for tau in TAU_GRID:
        errs = abs_errors(cases, var, "corrected", tau)
        pairs = [x for lead in range(1, 7) for x in errs.get(lead, [])]
        m = mae(pairs)
        if m is not None and (best is None or m < best[1]):
            best = (tau, m)
    return best[0] if best else 3


def bootstrap_diff(a, b, n=500, seed=1):
    """95 % interval of MAE(a) - MAE(b) on the same cases, resampled by day. a, b: [(day, err)] aligned."""
    days = sorted({d for d, _ in a})
    if len(days) < 3:
        return None
    by_a, by_b = defaultdict(list), defaultdict(list)
    for (d, ea), (_, eb) in zip(a, b):
        by_a[d].append(ea)
        by_b[d].append(eb)
    rnd = random.Random(seed)
    diffs = []
    for _ in range(n):
        sa = sb = cnt = 0
        for d in (rnd.choice(days) for _ in days):
            sa += sum(by_a[d]); sb += sum(by_b[d]); cnt += len(by_a[d])
        diffs.append((sa - sb) / cnt)
    diffs.sort()
    return round(diffs[int(0.025 * n)], 3), round(diffs[int(0.975 * n) - 1], 3)


def paired(cases, var, m1, m2, tau1=3, tau2=3):
    """{lead: (errs1, errs2)} on cases where both methods and the truth exist."""
    out = defaultdict(lambda: ([], []))
    for c in cases:
        f1, f2, truth = forecast(c, var, m1, tau1), forecast(c, var, m2, tau2), c["oL"].get(var)
        if None in (f1, f2, truth):
            continue
        d = c["t0"].date()
        out[c["lead"]][0].append((d, abs(f1 - truth)))
        out[c["lead"]][1].append((d, abs(f2 - truth)))
    return out


# ---- saturation deficit: over water at t >= 0, over ice at t < 0 ----

def e_water(t):
    return 6.112 * math.exp(17.62 * t / (243.12 + t))


def e_ice(t):
    return 6.112 * math.exp(22.46 * t / (272.62 + t))


def frost_point(td):
    """Frost point from the dew point (both over water as reported): e_ice(tf) = e_water(td)."""
    a = math.log(e_water(td) / 6.112)
    return 272.62 * a / (22.46 - a)


def sat_deficit(t, td):
    """t - dew point at t >= 0 C, t - frost point at t < 0 C. Fog below 0 C forms at saturation over ice,
    i.e. with rh (over water) below 100 % and t - td above 0 (about 1.25 C at -10 C)."""
    if t is None or td is None:
        return None
    td = min(td, t)
    return max(0.0, t - (frost_point(td) if t < 0 else td))


def temp_class(t):
    return None if t is None else ("t<0" if t < 0 else "t>=0")


# ---- fog probability (airports, against METAR) ----

def vis_index(vis_m):
    """0 at <= 100 m, 1 at >= 10 km (log scale); None when unknown."""
    if vis_m is None:
        return None
    return min(1.0, max(0.0, (math.log10(max(vis_m, 1)) - 2.0) / 2.0))


def fog_features(dd, ws, cl, night, fog0=None, vis0=None, lead=0, cold=None, vis_model=None):
    """Feature vector; missing dd/ws -> None (case skipped). fog0/vis0 are the METAR at t0: the fog
    probability starts from what is observed now and moves towards the model with the lead
    (interaction terms with lead/6). Without them only the model terms are used."""
    if None in (dd, ws):
        return None
    x = [1.0, dd / 5.0, ws / 10.0, (cl if cl is not None else 0.0) / 100.0, 1.0 if night else 0.0]  # rough scaling
    if fog0 is not None and vis0 is not None:
        f, v, l = (1.0 if fog0 else 0.0), vis_index(vis0), lead / 6.0
        x += [f, f * l, v, v * l, l]
    if cold is not None:  # forecast t < 0 C: fog persists and behaves differently in the cold
        c = 1.0 if cold else 0.0
        x += [c, c * (1.0 if fog0 else 0.0)]
    if vis_model is not None:  # ICON-EU visibility at the valid time (log index), and its weight growing with lead
        vm = vis_index(vis_model)
        x += [vm, vm * lead / 6.0]
    return x


def sigmoid(z):
    return 1 / (1 + math.exp(-max(-30, min(30, z))))


def fit_logistic(X, y, iters=400, lr=0.5, l2=1e-3):
    """Gradient descent on roughly scaled features. Uses numpy when installed (fast), else plain Python."""
    try:
        import numpy as np
    except ImportError:
        np = None
    if np is not None:
        A, b = np.asarray(X, float), np.asarray(y, float)
        w = np.zeros(A.shape[1])
        for _ in range(iters * 3):
            p = 1 / (1 + np.exp(-np.clip(A @ w, -30, 30)))
            w -= lr * (A.T @ (p - b) / len(b) + l2 * w)
        return [float(v) for v in w]
    w = [0.0] * len(X[0])
    n = len(X)
    for _ in range(iters):
        g = [0.0] * len(w)
        for xi, yi in zip(X, y):
            p = sigmoid(sum(a * b for a, b in zip(w, xi)))
            for j, xj in enumerate(xi):
                g[j] += (p - yi) * xj
        w = [wj - lr * (gj / n + l2 * wj) for wj, gj in zip(w, g)]
    return w


def predict(w, x):
    return sigmoid(sum(a * b for a, b in zip(w, x)))


def fog_rows(cases, method, taus, lat_lon, use_obs=True, use_cold=False, use_vis=False, vis_only=False):
    """(features, truth, fog_at_t0, lead, day, temp_class_of_truth) for airport cases with METAR fog flags.
    The deficit is to saturation over ice below 0 C (sat_deficit), from the forecast t and td.
    use_vis adds the ICON-EU visibility; vis_only uses nothing but it (calibrated raw model visibility).
    Cases are the same for all variants (METAR at t0 and model visibility required)."""
    rows = []
    for c in cases:
        if "fog" not in c["oL"] or "fog" not in c["o0"] or c["o0"].get("vis") is None or c["mL"].get("vis") is None:
            continue
        lat, lon = lat_lon[c["point"]]
        valid = c["t0"] + timedelta(hours=c["lead"])
        if vis_only:
            x = [1.0, vis_index(c["mL"]["vis"])]
        else:
            t = forecast(c, "t", method, taus.get("t", 3))
            td = forecast(c, "td", method, taus.get("td", 3))
            ws = forecast(c, "ws", method, taus.get("ws", 3))
            night = sun_elevation_deg(valid, lat, lon) <= 0
            if use_obs:
                x = fog_features(sat_deficit(t, td), ws, c["mL"].get("cl"), night, c["o0"]["fog"], c["o0"]["vis"],
                                 c["lead"], cold=(t is not None and t < 0) if use_cold else None,
                                 vis_model=c["mL"]["vis"] if use_vis else None)
            else:
                x = fog_features(sat_deficit(t, td), ws, c["mL"].get("cl"), night,
                                 vis_model=c["mL"]["vis"] if use_vis else None)
        if x is None:
            continue
        rows.append((x, 1.0 if c["oL"]["fog"] else 0.0, 1.0 if c["o0"]["fog"] else 0.0, c["lead"], c["t0"].date(),
                     temp_class(c["oL"].get("t"))))
    return rows


def bootstrap_fog(days, ps, pers, ys, n=500, seed=1):
    """95 % intervals, by day, of CSI(method) - CSI(persistence) and Brier(method) - Brier(persistence).
    ps are 0/1 decisions for CSI and probabilities for Brier: pass (decision, prob) pairs."""
    agg = defaultdict(lambda: [0, 0, 0, 0, 0, 0, 0.0, 0.0, 0])  # tp fp fn  tp_p fp_p fn_p  se se_p  n
    for d, (dec, prob), pp, y in zip(days, ps, pers, ys):
        a = agg[d]
        a[0] += dec and y; a[1] += dec and not y; a[2] += (not dec) and y
        a[3] += pp and y; a[4] += pp and not y; a[5] += (not pp) and y
        a[6] += (prob - y) ** 2; a[7] += (pp - y) ** 2; a[8] += 1
    keys = sorted(agg)
    if len(keys) < 3:
        return None
    rnd = random.Random(seed)
    dcsi, dbr = [], []
    for _ in range(n):
        s = [0] * 9
        for k in (rnd.choice(keys) for _ in keys):
            s = [x + y for x, y in zip(s, agg[k])]
        c1 = s[0] / (s[0] + s[1] + s[2]) if s[0] + s[1] + s[2] else 0.0
        c0 = s[3] / (s[3] + s[4] + s[5]) if s[3] + s[4] + s[5] else 0.0
        dcsi.append(c1 - c0)
        dbr.append((s[6] - s[7]) / s[8])
    dcsi.sort(); dbr.sort()
    lo, hi = int(0.025 * n), int(0.975 * n) - 1
    return {"csi_diff_ci95": [round(dcsi[lo], 3), round(dcsi[hi], 3)],
            "brier_diff_ci95": [round(dbr[lo], 5), round(dbr[hi], 5)]}


def brier(ps, ys):
    return sum((p - y) ** 2 for p, y in zip(ps, ys)) / len(ys) if ys else None


def contingency(ps, ys, thr=0.5):
    tp = sum(1 for p, y in zip(ps, ys) if p >= thr and y)
    fp = sum(1 for p, y in zip(ps, ys) if p >= thr and not y)
    fn = sum(1 for p, y in zip(ps, ys) if p < thr and y)
    return {"events": tp + fn, "tp": tp, "fp": fp, "fn": fn,
            "pod": round(tp / (tp + fn), 3) if tp + fn else None,
            "far": round(fp / (tp + fp), 3) if tp + fp else None,
            "csi": round(tp / (tp + fp + fn), 3) if tp + fp + fn else None}
