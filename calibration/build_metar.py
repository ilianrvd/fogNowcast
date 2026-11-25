"""Calibrate the fog flag of NTU stations against airport METAR:
python -m calibration.build_metar --archive-dir DIR --out DIR

Fetches METAR/SPECI of the five airports from IEM, pairs them with half-hourly NTU observations of the
stations near each airport (static/correction_stations.json), writes metar_<ICAO>.csv.gz,
calibration_metar.json and calibration_report.md. Run in GitHub Actions.
"""
import argparse
import csv
import glob
import gzip
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from correction.hourly import slot_obs

from . import skill
from .metar_iem import fetch_reports

SOURCE = ("METAR/SPECI: Iowa Environmental Mesonet ASOS archive; stations: NTU (bgtoll.bg, CC BY 4.0); "
          "fogNowcast docs/obs_metar_synop.md")


def airport_stations(stations_json):
    out = {}
    for s in stations_json["stations"]:
        for g in s["groups"]:
            if g.startswith("airport:"):
                out.setdefault(g.split(":")[1], []).append(s["id"])
    return out


def write_reports(path, reports):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "vis_m", "vis_source", "wx", "fog", "low_vis", "t", "td", "wind_kt", "wind_dir", "raw"])
        for r in reports:
            w.writerow([r["time"].strftime("%Y-%m-%dT%H:%M:%SZ"), r["vis_m"], r["vis_source"], " ".join(r["wx"]),
                        int(r["fog"]), int(r["low_vis"]), r["t"], r["td"], r["wind_kt"], r["wind_dir"], r["raw"]])


def report_md(result):
    L = ["# Калибриране на флага за мъгла (станции на НТУ срещу METAR)", "",
         f"Период: {result['period'][0]} … {result['period'][1]}. Мъгла = видимост < 1000 m с FG/FZFG; ниска видимост = < 5000 m. "
         f"Показани са най-добрите три комбинации по CSI при поне {skill.MIN_EVENTS} случая, и базовият праг `rh>=95`. "
         "POD = дял на хванатите случаи, FAR = дял на фалшивите тревоги, CSI = TP/(TP+FP+FN). Слотове по 30 мин.", ""]
    for icao, a in result["airports"].items():
        L += [f"## {icao}", f"Станции: {', '.join(a['stations'])}. METAR от {a['metar_first']}; слотове с METAR: {a['slots']}; "
              f"слотове с мъгла: {a['fog_slots']}; с ниска видимост: {a['low_vis_slots']}.", "",
              "| цел | подмножество | станция | предиктор | случаи | POD | FAR | CSI |", "|---|---|---|---|---|---|---|---|"]
        for target in ("fog", "low_vis"):
            for sub in ("all", "cold", "night"):
                rows = skill.top_rows(a["rows"], target, sub)
                base = [r for r in a["rows"] if r["target"] == target and r["subset"] == sub
                        and r["predictor"] == "rh>=95" and r["station"] == ("any" if len(a["stations"]) > 1 else a["stations"][0])]
                for tag, r in [("", x) for x in rows] + [("база ", x) for x in base]:
                    L.append(f"| {target} | {sub} | {r['station']} | {tag}{r['predictor']} | {r['events']} | "
                             f"{r['pod']} | {r['far']} | {r['csi']} |")
        L.append("")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--stations", default="static/correction_stations.json")
    ap.add_argument("--points", default="static/points.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2026-01-27")
    ap.add_argument("--end", default="2026-11-25", help="exclusive")
    a = ap.parse_args(argv)

    stations_json = json.loads(Path(a.stations).read_text(encoding="utf-8"))
    by_airport = airport_stations(stations_json)
    points = {p["id"]: p for p in json.loads(Path(a.points).read_text(encoding="utf-8"))["points"]
              if p.get("type") == "airport"}
    paths = sorted(glob.glob(str(Path(a.archive_dir) / "ntu_*.csv.gz")))
    if not paths:
        print("no archive files", file=sys.stderr)
        return 1
    all_ids = sorted({s for v in by_airport.values() for s in v})
    obs = slot_obs(paths, all_ids, step_min=skill.SLOT_MIN)
    print(f"slot observations: {len(obs)}")

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    result = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "source": SOURCE,
              "period": [a.start, a.end], "slot_minutes": skill.SLOT_MIN, "min_events": skill.MIN_EVENTS,
              "targets": {"fog": "vis < 1000 m and FG/FZFG", "low_vis": "vis < 5000 m"},
              "airports": {}}
    for icao, ids in sorted(by_airport.items()):
        reports = fetch_reports(icao, a.start, a.end)
        write_reports(out / f"metar_{icao}.csv.gz", reports)
        slots = skill.metar_by_slot(reports)
        p = points[icao]
        rows = skill.evaluate_airport(slots, obs, ids, p["lat"], p["lon"])
        fallback = sum(1 for r in reports if r["vis_source"] == "iem")
        result["airports"][icao] = {
            "stations": ids, "metar_reports": len(reports), "metar_first": min(r["time"] for r in reports).strftime("%Y-%m-%d"),
            "vis_from_iem_rounded": fallback, "slots": len(slots),
            "fog_slots": sum(v["fog"] for v in slots.values()), "low_vis_slots": sum(v["low_vis"] for v in slots.values()),
            "rows": rows}
        print(f"{icao}: reports={len(reports)} slots={len(slots)} fog_slots={result['airports'][icao]['fog_slots']} "
              f"vis_from_iem={fallback}", flush=True)
        time.sleep(2)
    (out / "calibration_metar.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    md = report_md(result)
    (out / "calibration_report.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
