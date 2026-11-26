"""Stage-2 verification run: python -m verification.build --archive-dir DIR --metar-dir DIR --out DIR

Raw ICON-EU (Open-Meteo single runs) vs persistence vs residual correction, leads 0-6 h, at the five
airports (truth: METAR) and the 21 NTU stations (truth: NTU). tau and the fog model are fitted on the
training period and scored on the test period. Research only (docs/verification.md).
"""
import argparse
import csv
import glob
import gzip
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from correction.hourly import hourly_obs, parse_time

from . import core
from .runs import fetch_run, from_json, to_json

UTC = timezone.utc
SOURCE = ("ICON-EU single runs (Open-Meteo Single Runs API); NTU archive (bgtoll.bg, CC BY 4.0); METAR (Iowa "
          "Environmental Mesonet); fogNowcast docs/verification.md")


def metar_hourly(path, icao):
    """{(icao, hour): obs} from metar_<ICAO>.csv.gz (analysis-metar); report nearest to HH:00 within 15 min."""
    best = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            t = parse_time(r["time"])
            hour = (t + timedelta(minutes=30)).replace(minute=0, second=0)
            off = abs((t - hour).total_seconds())
            if off > 900:
                continue
            k = (icao, hour)
            if k not in best or off < best[k][0]:
                num = lambda x: float(x) if x not in ("", "None") else None
                tt, td, ws = num(r["t"]), num(r["td"]), num(r["wind_kt"])
                best[k] = (off, {"t": tt, "td": td, "dd": tt - td if tt is not None and td is not None else None,
                                 "ws": ws, "fog": r["fog"] == "1", "vis": num(r["vis_m"])})
    return {k: v[1] for k, v in best.items()}


def hours(start, end):
    t, out = start, []
    while t <= end:
        out.append(t)
        t += timedelta(hours=1)
    return out


WORKERS = 2  # parallel requests to Open-Meteo (4 gave HTTP 429 on 2026-11-26)


def load_runs(run_times, points, cache_dir):
    """Fetch runs in parallel; runs already in cache_dir (from an earlier attempt) are not requested again.
    Prints one line per run so a slow run shows in the log."""
    model, failed = {}, []
    todo = []
    for r in run_times:
        f = Path(cache_dir) / f"{r:%Y%m%d%H}.json.gz" if cache_dir else None
        if f and f.exists():
            with gzip.open(f, "rt", encoding="utf-8") as fh:
                model[r] = from_json(json.load(fh))
        else:
            todo.append((r, f))
    print(f"runs: {len(model)} from cache, {len(todo)} to fetch", flush=True)
    t_start = time.time()

    def one(r):
        t = time.time()
        return fetch_run(r, points), time.time() - t

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(one, r): (r, f) for r, f in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            r, f = futs[fut]
            try:
                data, dt = fut.result()
                model[r] = data
                if f:
                    f.parent.mkdir(parents=True, exist_ok=True)
                    with gzip.open(f, "wt", encoding="utf-8") as fh:
                        json.dump(to_json(data), fh)
                print(f"[{i}/{len(todo)}] {r:%Y-%m-%dT%HZ} ok {dt:.1f} s (total {time.time() - t_start:.0f} s)", flush=True)
            except Exception as e:  # noqa: BLE001 - a missing run only removes its cases
                failed.append(r.strftime("%Y-%m-%dT%HZ"))
                print(f"[{i}/{len(todo)}] {r:%Y-%m-%dT%HZ} FAILED: {type(e).__name__}: {e}", flush=True)
    return model, sorted(model), sorted(failed)


def score_continuous(train, test, groups):
    res = {}
    for var in core.VARS:
        tau = core.fit_tau(train, var)
        res[var] = {"tau_h": tau, "groups": {}}
        for gname, pred in groups.items():
            cs = [c for c in test if pred(c)]
            rows = []
            for lead in core.LEADS:
                row = {"lead_h": lead}
                pm = core.paired([c for c in cs if c["lead"] == lead], var, "corrected", "model", tau)[lead]
                pp = core.paired([c for c in cs if c["lead"] == lead], var, "corrected", "persistence", tau)[lead]
                row["n"] = len(pm[0])
                row["mae_corrected"] = round(core.mae(pm[0]), 3) if pm[0] else None
                row["mae_model"] = round(core.mae(pm[1]), 3) if pm[1] else None
                row["mae_persistence"] = round(core.mae(pp[1]), 3) if pp[1] else None
                row["diff_vs_model_ci95"] = core.bootstrap_diff(*pm) if pm[0] else None
                row["diff_vs_persistence_ci95"] = core.bootstrap_diff(*pp) if pp[0] else None
                rows.append(row)
            res[var]["groups"][gname] = rows
    return res


FOG_VARIANTS = {  # name: (method for t/td/ws, use the METAR at t0)
    "model": ("model", False),
    "corrected": ("corrected", False),
    "corrected+obs": ("corrected", True),
}


def score_fog(train, test, airports, lat_lon, taus):
    out = {}
    for name, (method, use_obs) in FOG_VARIANTS.items():
        tr = core.fog_rows([c for c in train if c["point"] in airports], method, taus, lat_lon, use_obs)
        te = core.fog_rows([c for c in test if c["point"] in airports], method, taus, lat_lon, use_obs)
        if not tr or not te or not any(y for _, y, *_ in tr):
            out[name] = {"note": "too few cases or no fog in training"}
            continue
        w = core.fit_logistic([x for x, *_ in tr], [y for _, y, *_ in tr])
        ptr, ytr = [core.predict(w, x) for x, *_ in tr], [y for _, y, *_ in tr]
        thr_best = max((t / 20 for t in range(1, 20)), key=lambda t: core.contingency(ptr, ytr, t)["csi"] or 0)
        rows = []
        for lead in core.LEADS:
            sel = [r for r in te if r[3] == lead]
            ps = [core.predict(w, r[0]) for r in sel]
            ys = [r[1] for r in sel]
            pers = [r[2] for r in sel]
            rows.append({"lead_h": lead, "n": len(sel), "fog_events": int(sum(ys)),
                         "brier": round(core.brier(ps, ys), 4) if ys else None,
                         "brier_persistence": round(core.brier(pers, ys), 4) if ys else None,
                         "scores": core.contingency(ps, ys, thr_best),
                         "scores_persistence": core.contingency(pers, ys, 0.5)})
        by_temp = {}
        for tc in ("t<0", "t>=0"):
            sel = [r for r in te if r[5] == tc and r[3] >= 1]          # leads 1-6 together
            ps, ys, pers = [core.predict(w, r[0]) for r in sel], [r[1] for r in sel], [r[2] for r in sel]
            by_temp[tc] = {"n": len(sel), "fog_events": int(sum(ys)),
                           "brier": round(core.brier(ps, ys), 4) if ys else None,
                           "brier_persistence": round(core.brier(pers, ys), 4) if ys else None,
                           "scores": core.contingency(ps, ys, thr_best),
                           "scores_persistence": core.contingency(pers, ys, 0.5)}
        out[name] = {"weights": [round(x, 3) for x in w], "threshold": thr_best, "train_rows": len(tr),
                     "train_fog": int(sum(ytr)), "by_lead": rows, "by_temp_leads_1_6": by_temp}
    return out


def report(res):
    L = ["# Проверка на етап 2 (изследване, не за оперативна употреба)", "",
         f"Обучение: {res['train'][0]} … {res['train'][1]} (мъгла: {res['fog_train'][0]} … {res['fog_train'][1]}); проверка: {res['test'][0]} … {res['test'][1]}. "
         f"Рън: последният с начало ≥ {core.LATENCY_H} ч преди t0. MAE на проверочния период; "
         "`ИД` = 95 % интервал на разликата „корекция − вариант“ (bootstrap по дни; изцяло под 0 = корекцията е по-добра). "
         "При мъглата дефицитът е до насищане спрямо лед под 0 °C; групите по температура са по наблюдението в срока.", ""]
    for var, v in res["continuous"].items():
        L.append(f"## {var} (τ = {v['tau_h']} ч)")
        for g, rows in v["groups"].items():
            L += [f"### {g}", "", "| срок | n | корекция | модел | устойчивост | ИД спрямо модела | ИД спрямо устойчивостта |",
                  "|---|---|---|---|---|---|---|"]
            for r in rows:
                L.append(f"| {r['lead_h']} | {r['n']} | {r['mae_corrected']} | {r['mae_model']} | {r['mae_persistence']} | "
                         f"{r['diff_vs_model_ci95']} | {r['diff_vs_persistence_ci95']} |")
            L.append("")
    L.append("## Мъгла на летищата (METAR: видимост < 1000 m с FG/FZFG)")
    for m, v in res["fog"].items():
        if "by_lead" not in v:
            L += [f"- {m}: {v.get('note')}"]
            continue
        L += ["", f"### p_fog: {m} (праг {v['threshold']}; обучение {v['train_rows']} реда, {v['train_fog']} с мъгла)", "",
              "| срок | n | случаи | Brier | Brier устойч. | POD | FAR | CSI | CSI устойч. |", "|---|---|---|---|---|---|---|---|---|"]
        for r in v["by_lead"]:
            s, sp = r["scores"], r["scores_persistence"]
            L.append(f"| {r['lead_h']} | {r['n']} | {r['fog_events']} | {r['brier']} | {r['brier_persistence']} | "
                     f"{s['pod']} | {s['far']} | {s['csi']} | {sp['csi']} |")
        for tc, r in v["by_temp_leads_1_6"].items():
            s, sp = r["scores"], r["scores_persistence"]
            L.append(f"| 1–6, {tc} | {r['n']} | {r['fog_events']} | {r['brier']} | {r['brier_persistence']} | "
                     f"{s['pod']} | {s['far']} | {s['csi']} | {sp['csi']} |")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--metar-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--train", default="2026-10-01:2026-10-31")
    ap.add_argument("--test", default="2026-11-01:2026-11-24")
    ap.add_argument("--fog-train", default="2026-04-05:2026-10-31",
                    help="training period of the fog model (airports, METAR); longer than --train: fog is rare")
    ap.add_argument("--cache-dir", default=None, help="keep fetched runs here and reuse them")
    a = ap.parse_args(argv)
    tr = [parse_time(x + "T00:00:00Z") for x in a.train.split(":")]
    te = [parse_time(x + "T00:00:00Z") for x in a.test.split(":")]
    ftr = [parse_time(x + "T00:00:00Z") for x in a.fog_train.split(":")]
    start, end = min(tr[0], ftr[0]), te[1] + timedelta(hours=23)

    airports = [p for p in json.loads(Path("static/points.json").read_text(encoding="utf-8"))["points"]
                if p["type"] == "airport"]
    stations = json.loads(Path("static/correction_stations.json").read_text(encoding="utf-8"))["stations"]
    points = [{"id": p["id"], "lat": p["lat"], "lon": p["lon"], "elev_m": p["elev_m"]} for p in airports + stations]
    lat_lon = {p["id"]: (p["lat"], p["lon"]) for p in points}

    obs = hourly_obs(sorted(glob.glob(str(Path(a.archive_dir) / "ntu_*.csv.gz"))), [s["id"] for s in stations])
    for p in airports:
        f = Path(a.metar_dir) / f"metar_{p['id']}.csv.gz"
        if f.exists():
            obs.update(metar_hourly(f, p["id"]))
    print(f"observations: {len(obs)}", flush=True)

    run_times = []
    r = (start - timedelta(hours=12)).replace(hour=0)
    while r <= end:
        if r.hour in core.RUN_HOURS:
            run_times.append(r)
        r += timedelta(hours=6)
    model, runs, failed = load_runs(run_times, points, a.cache_dir)
    print(f"runs: {len(runs)} ok, {len(failed)} failed", flush=True)

    ids = [p["id"] for p in points]
    cases = core.build_cases(hours(start, end), obs, model, runs, ids)
    train = [c for c in cases if tr[0] <= c["t0"] < tr[1] + timedelta(days=1)]
    test = [c for c in cases if te[0] <= c["t0"] < te[1] + timedelta(days=1)]
    print(f"cases: train={len(train)} test={len(test)}", flush=True)
    ap_ids, st_ids = {p["id"] for p in airports}, {s["id"] for s in stations}
    tcls = lambda c: core.temp_class(c["oL"].get("t"))
    groups = {"летища (METAR)": lambda c: c["point"] in ap_ids,
              "станции НТУ": lambda c: c["point"] in st_ids,
              "всички, t < 0 °C": lambda c: tcls(c) == "t<0",
              "всички, t ≥ 0 °C": lambda c: tcls(c) == "t>=0"}
    cont = score_continuous(train, test, groups)
    fog_train = [c for c in cases if ftr[0] <= c["t0"] < ftr[1] + timedelta(days=1)]
    print(f"fog training cases (all points): {len(fog_train)}", flush=True)
    fog = score_fog(fog_train, test, {p["id"] for p in airports}, lat_lon, {v: cont[v]["tau_h"] for v in cont})
    res = {"generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": SOURCE,
           "run": "ICON-EU single runs 00/06/12/18 UTC", "status": "research, not for operational use",
           "train": a.train.split(":"), "fog_train": a.fog_train.split(":"), "test": a.test.split(":"),
           "latency_h": core.LATENCY_H,
           "runs_failed": failed, "continuous": cont, "fog": fog}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "verification.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    md = report(res)
    (out / "verification_report.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
