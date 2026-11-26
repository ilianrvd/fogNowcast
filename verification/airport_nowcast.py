"""Verification of airport-nowcast's fog probability (p_lt1000) and visibility against METAR and persistence.

python -m verification.airport_nowcast --tables DIR --metar-dir DIR --out DIR

Reads the archived tables of ilianrvd/airport-nowcast (YYYY/MM/DD/table_HHMM.json.gz, public archive
branches) and the METAR files of analysis-metar (IEM). Read-only; nothing in airport-nowcast is changed.
Same scores as docs/verification.md: by lead 1-6 h, against persistence (the METAR at t0 stays), with
95 % intervals by day and reliability. Research only.
"""
import argparse
import csv
import glob
import gzip
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from . import core

UTC = timezone.utc
LEADS = {"+1:00": 1, "+2:00": 2, "+3:00": 3, "+4:00": 4, "+5:00": 5, "+6:00": 6}
THRESHOLDS = (0.3, 0.6)  # airport-nowcast categories: УМЕР >= 30, ВИС >= 60
REL_BINS = ((0, 0.1), (0.1, 0.3), (0.3, 0.6), (0.6, 1.01))
SOURCE = ("airport-nowcast archived tables (github.com/ilianrvd/airport-nowcast, archive branches); METAR (Iowa "
          "Environmental Mesonet); fogNowcast docs/verification.md")


def parse_t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(UTC)


def load_metar(metar_dir):
    """{(icao, slot): {"vis": m, "fog": bool}} on a 30-min grid (report nearest to the slot, within 15 min)."""
    out = {}
    for path in glob.glob(os.path.join(metar_dir, "metar_*.csv.gz")):
        icao = os.path.basename(path)[6:10]
        best = {}
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if r["vis_m"] in ("", "None"):
                    continue
                t = parse_t(r["time"])
                s = t.timestamp()
                slot_s = int((s + 900) // 1800) * 1800
                off = abs(s - slot_s)
                if off > 900:
                    continue
                k = (icao, datetime.fromtimestamp(slot_s, tz=UTC))
                if k not in best or off < best[k][0]:
                    best[k] = (off, {"vis": float(r["vis_m"]), "fog": r["fog"] == "1"})
        out.update({k: v[1] for k, v in best.items()})
    return out


def collect(tables_dir, metar):
    """Rows (icao, day, lead, p, vis_fc, truth_lt1000, truth_fog, pers_lt1000, pers_fog) for every archived
    table, airport and lead 1-6 h with a METAR at t0 and at the valid time. Tables with a stale METAR are
    skipped (their t0 is not an observation)."""
    rows, tables, stale = [], 0, 0
    for path in sorted(glob.glob(os.path.join(tables_dir, "**", "table_*.json.gz"), recursive=True)):
        try:
            with gzip.open(path, "rt", encoding="utf-8") as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        tables += 1
        for icao, a in (d.get("airports") or {}).items():
            if a.get("stale_metar"):
                stale += 1
                continue
            t0 = parse_t(a["t0"])
            o0 = metar.get((icao, t0))
            if o0 is None:
                continue
            for c in a.get("cols", []):
                lead = LEADS.get(c.get("lead"))
                if lead is None or c.get("p_lt1000") is None:
                    continue
                oL = metar.get((icao, t0 + timedelta(hours=lead)))
                if oL is None:
                    continue
                rows.append((icao, t0.date(), lead, c["p_lt1000"] / 100.0, c.get("vis_m"),
                             oL["vis"] < 1000, oL["fog"], o0["vis"] < 1000, o0["fog"]))
    return rows, tables, stale


def score(rows, target):
    """Scores by lead for target 'lt1000' (any visibility < 1000 m) or 'fog' (< 1000 m with FG/FZFG)."""
    ti, pi = (5, 7) if target == "lt1000" else (6, 8)
    out = []
    for lead in range(1, 7):
        sel = [r for r in rows if r[2] == lead]
        if not sel:
            continue
        ys = [1.0 if r[ti] else 0.0 for r in sel]
        ps = [r[3] for r in sel]
        pers = [1.0 if r[pi] else 0.0 for r in sel]
        days = [r[1] for r in sel]
        row = {"lead_h": lead, "n": len(sel), "events": int(sum(ys)),
               "brier": round(core.brier(ps, ys), 4), "brier_persistence": round(core.brier(pers, ys), 4),
               "mean_p": round(sum(ps) / len(ps), 4), "obs_freq": round(sum(ys) / len(ys), 4),
               "scores_persistence": core.contingency(pers, ys, 0.5),
               "vis_lt1000": core.contingency([1.0 if (r[4] is not None and r[4] < 1000) else 0.0 for r in sel], ys, 0.5)}
        for thr in THRESHOLDS:
            dec = [p >= thr for p in ps]
            row[f"p>={thr}"] = core.contingency(dec, ys, 0.5)
            row[f"p>={thr}_ci95"] = core.bootstrap_fog(days, list(zip(dec, ps)), pers, ys)
        rel = []
        for lo, hi in REL_BINS:
            b = [(p, y) for p, y in zip(ps, ys) if lo <= p < hi]
            rel.append({"p_from": lo, "p_to": min(hi, 1.0), "n": len(b),
                        "mean_p": round(sum(p for p, _ in b) / len(b), 3) if b else None,
                        "obs_freq": round(sum(y for _, y in b) / len(b), 3) if b else None})
        row["reliability"] = rel
        out.append(row)
    return out


def report(res):
    L = ["# Проверка на p_lt1000 на airport-nowcast (изследване, не за оперативна употреба)", "",
         f"Таблици: {res['tables']} ({res['period'][0]} … {res['period'][1]}), случаи летище × срок: {res['rows']}. "
         "Устойчивост = METAR в t0 остава. ИД = 95 % интервал по дни на разликата с устойчивостта "
         "(CSI: изцяло над 0 = по-добро; Brier: изцяло под 0 = по-добро).", ""]
    for target, title in (("lt1000", "видимост < 1000 m (всякаква причина)"), ("fog", "мъгла: < 1000 m с FG/FZFG")):
        L += [f"## Цел: {title}", "",
              "| срок | n | случаи | Brier | Brier устойч. | CSI p≥0,3 | CSI p≥0,6 | CSI vis_m<1000 | CSI устойч. | ИД CSI (p≥0,3) | ИД Brier | ср. p / честота |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in res[target]:
            ci = r["p>=0.3_ci95"] or {}
            L.append(f"| {r['lead_h']} | {r['n']} | {r['events']} | {r['brier']} | {r['brier_persistence']} | "
                     f"{r['p>=0.3']['csi']} | {r['p>=0.6']['csi']} | {r['vis_lt1000']['csi']} | {r['scores_persistence']['csi']} | "
                     f"{ci.get('csi_diff_ci95')} | {ci.get('brier_diff_ci95')} | {r['mean_p']} / {r['obs_freq']} |")
        L += ["", "Надеждност при срок 3 ч (обявена вероятност срещу реална честота):", ""]
        r3 = next((r for r in res[target] if r["lead_h"] == 3), None)
        if r3:
            L += ["| p | n | средна p | честота |", "|---|---|---|---|"]
            for b in r3["reliability"]:
                L.append(f"| {b['p_from']}–{b['p_to']} | {b['n']} | {b['mean_p']} | {b['obs_freq']} |")
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--metar-dir", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    metar = load_metar(a.metar_dir)
    rows, tables, stale = collect(a.tables, metar)
    if not rows:
        print("no cases", file=sys.stderr)
        return 1
    days = sorted({r[1] for r in rows})
    res = {"generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": SOURCE, "run": None,
           "status": "research, not for operational use", "tables": tables, "stale_metar_skipped": stale,
           "rows": len(rows), "period": [days[0].isoformat(), days[-1].isoformat()],
           "lt1000": score(rows, "lt1000"), "fog": score(rows, "fog")}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "airport_nowcast_verification.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    md = report(res)
    with open(os.path.join(a.out, "airport_nowcast_report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
