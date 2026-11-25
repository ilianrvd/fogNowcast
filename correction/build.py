"""Build the per-station correction: python -m correction.build --archive-dir DIR --out DIR

Reads ntu_YYYY-MM.csv.gz from --archive-dir (months of the training and test periods), fetches
ICON-EU values by station from Open-Meteo, fits on the training period, evaluates on the test period.
Writes correction_by_station.json and correction_report.md. Run in GitHub Actions.
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import fit
from .hourly import hourly_obs, parse_time
from .openmeteo import fetch_station

SOURCE = ("NTU archive (bgtoll.bg, CC BY 4.0) vs ICON-EU from Open-Meteo Historical Forecast; "
          "fogNowcast docs/correction_model.md")


def months(start, end):
    y, m = int(start[:4]), int(start[5:7])
    out = []
    while f"{y:04d}-{m:02d}" <= end[:7]:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def split(pairs, start, end):
    a, b = parse_time(start + "T00:00:00Z"), parse_time(end + "T23:59:59Z")
    return [p for p in pairs if a <= p["time"] <= b]


def report_md(meta, ev):
    lines = [f"# Корекция на ICON-EU по станции", "", "**Изследване, не за оперативна употреба** (виж docs/correction_model.md).", "",
             f"Обучение: {meta['train'][0]} … {meta['train'][1]}; проверка: {meta['test'][0]} … {meta['test'][1]}. "
             f"Минимум {fit.MIN_N} двойки на клетка. `+` = поправката е приета (MAE намалява в проверката).", "",
             "| станция | вел. | n | MAE преди | MAE след | bias преди | bias след | приета | наситен+тих n | MAE преди | MAE след |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for sid in sorted(ev, key=lambda s: int(s.split(":")[1])):
        for var in fit.VARS:
            r = ev[sid].get(var)
            if not r:
                continue
            f = r["sat_calm"]
            lines.append(f"| {sid} {meta['names'][sid]} | {var} | {r['n']} | {r['mae_before']} | {r['mae_after']} | "
                         f"{r['bias_before']} | {r['bias_after']} | {'+' if r['accepted'] else '-'} | "
                         f"{f['n']} | {f['mae_before']} | {f['mae_after']} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--stations", default="static/correction_stations.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--train-start", default="2026-01-27")
    ap.add_argument("--train-end", default="2026-03-31")
    ap.add_argument("--test-start", default="2026-11-01")
    ap.add_argument("--test-end", default="2026-11-24")
    a = ap.parse_args(argv)

    stations = {s["id"]: s for s in json.loads(Path(a.stations).read_text(encoding="utf-8"))["stations"]}
    paths = []
    for period in ((a.train_start, a.train_end), (a.test_start, a.test_end)):
        for m in months(*period):
            p = Path(a.archive_dir) / f"ntu_{m}.csv.gz"
            if not p.exists():
                print(f"missing archive month {m}", file=sys.stderr)
                return 1
            paths.append(p)
    paths = sorted(set(paths))
    obs = hourly_obs(paths, stations)
    print(f"hourly observations: {len(obs)}")

    model = {}
    for sid, s in stations.items():
        model[sid] = fetch_station(s["lat"], s["lon"], s["elev_m"], a.train_start, a.test_end)
        print(f"model {sid}: {len(model[sid])} hours", flush=True)
        time.sleep(1)

    pairs = fit.build_pairs(obs, model, stations)
    train = split(pairs, a.train_start, a.train_end)
    test = split(pairs, a.test_start, a.test_end)
    print(f"pairs: train={len(train)} test={len(test)}")
    cells = fit.fit_cells(train)
    ev = fit.evaluate(test, cells)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {"train": [a.train_start, a.train_end], "test": [a.test_start, a.test_end],
            "names": {k: v["name"] for k, v in stations.items()}}
    result = {"generated_at": now, "source": SOURCE, "model": "icon_eu (Open-Meteo Historical Forecast)",
              "train_period": meta["train"], "test_period": meta["test"], "min_n": fit.MIN_N,
              "regime_axes": {"day_night": "sun elevation > 0", "wind": f"obs wind < {fit.CALM_KT} kt = calm",
                              "saturation": f"obs rh >= {fit.SAT_RH} % = sat"},
              "stations": {sid: {"name": stations[sid]["name"], "cells": cells.get(sid, {}),
                                 "test": ev.get(sid, {})} for sid in stations}}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "correction_by_station.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    md = report_md(meta, ev)
    (out / "correction_report.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
