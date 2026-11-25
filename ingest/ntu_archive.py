"""Import the NTU open-data archive (Dropbox folder zip) into normalised monthly tables.

Run from GitHub Actions (see .github/workflows/import-ntu-archive.yml) or locally on a downloaded zip:
    python -m ingest.ntu_archive --zip "Meteo data.zip" --out out
    python -m ingest.ntu_archive --download --out out        # Actions only

Output (see docs/archive_ntu.md): ntu_YYYY-MM.csv.gz per month, ntu_stations.json, ntu_import_meta.json.
Time comes only from the record's `time` (UTC), never from the file name.
"""
import argparse
import csv
import gzip
import io
import json
import os
import re
import sys
import tempfile
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from qc.ntu_qc import RH_SATURATED, classify_lag, normalize_record

FOLDER = ("https://www.dropbox.com/scl/fo/3v3asm04vcf3oahhgb39w/AJ8-3EDvwDf7d3a6OGir1wo"
          "?rlkey=i95zfngdxye3lj6zwrk97cgg5&st=4pyajkii&dl=1")
UA = "fogNowcast-import/0.1 (+https://github.com/ilianrvd/fogNowcast)"
SOURCE = "bgtoll.bg/otvoreni-danni (Национално тол управление), CC BY 4.0"
SOFIA = ZoneInfo("Europe/Sofia")
UTC = timezone.utc
COLUMNS = ["time", "id", "t", "rh", "td", "t_surface", "pressure_hpa", "wind_kt", "wind_dir", "flags"]
_SNAP = re.compile(r"stationsData_(\d{8})_(\d{4})\.json$")


def download(path: str) -> int:
    req = urllib.request.Request(FOLDER, headers={"User-Agent": UA})
    total = 0
    with urllib.request.urlopen(req, timeout=300) as resp, open(path, "wb") as out:
        while True:
            b = resp.read(1 << 20)
            if not b:
                return total
            out.write(b)
            total += len(b)


def snapshot_candidates(member: str) -> list[datetime]:
    """File names are local time (Europe/Sofia). The repeated hour at the end of DST is ambiguous,
    so return both interpretations."""
    m = _SNAP.search(member)
    if not m:
        raise ValueError(f"unexpected snapshot name: {member}")
    naive = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M")
    out = []
    for fold in (0, 1):
        t = naive.replace(tzinfo=SOFIA, fold=fold).astimezone(UTC)
        if t not in out:
            out.append(t)
    return out


def lag_minutes(candidates: list[datetime], record_time: datetime) -> float:
    lags = [(c - record_time).total_seconds() / 60 for c in candidates]
    fresh = [x for x in lags if classify_lag(x) == "ok"]
    return min(fresh, key=abs) if fresh else min(lags, key=abs)


def fmt(v, nd):
    return "" if v is None else f"{v:.{nd}f}"


def import_archive(zip_path: str, out_dir: str) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    outer = zipfile.ZipFile(zip_path)
    days = sorted((i for i in outer.infolist() if not i.is_dir() and i.filename.endswith(".zip")),
                  key=lambda i: i.filename.rsplit("/", 1)[-1])
    seen = set()
    rows = []   # compact tuples: (epoch_min, sid, t, rh, td, t_surface, pressure, wind_kt, wind_dir, flags)
    last_meta = {}   # sid -> (time, name, name_en, lat, lon)
    counts = Counter()
    bad_files = []
    per_station = defaultdict(Counter)
    for info in days:
        inner = zipfile.ZipFile(io.BytesIO(outer.read(info)))
        for member in sorted(n for n in inner.namelist() if n.endswith(".json")):
            try:
                cands = snapshot_candidates(member)
                data = json.loads(inner.read(member).decode("utf-8-sig"))
                if not isinstance(data, list):
                    raise ValueError("not a list")
            except Exception as e:  # noqa: BLE001 - count and move on
                bad_files.append(f"{info.filename}:{member}:{type(e).__name__}")
                continue
            counts["snapshots"] += 1
            for rec in data:
                counts["records_seen"] += 1
                try:
                    n = normalize_record(rec)
                except Exception:  # noqa: BLE001
                    counts["dropped_invalid"] += 1
                    continue
                sid = n["station_id"]
                verdict = classify_lag(lag_minutes(cands, n["time"]))
                if verdict != "ok":
                    counts[f"dropped_{verdict}"] += 1
                    per_station[sid][f"dropped_{verdict}"] += 1
                    continue
                key = (sid, int(n["time"].timestamp()) // 60)
                if key in seen:
                    counts["dropped_duplicate"] += 1
                    continue
                seen.add(key)
                rows.append((key[1], sid, n["t"], n["rh"], n["td"], n["t_surface"], n["pressure_hpa"],
                             n["wind_kt"], n["wind_dir"], tuple(n["flags"])))
                if sid not in last_meta or n["time"] >= last_meta[sid][0]:
                    last_meta[sid] = (n["time"], n["name"], n["name_en"], n["lat"], n["lon"])
    rows.sort(key=lambda r: (r[0], r[1]))

    stats = defaultdict(lambda: {"n": 0, "flags": Counter(), "repeat": 0, "sat": 0})
    prev = {}
    by_month = defaultdict(list)
    for r in rows:
        month = datetime.fromtimestamp(r[0] * 60, UTC).strftime("%Y-%m")
        by_month[month].append(r)
    del rows
    written = {}
    for month in sorted(by_month):
        path = os.path.join(out_dir, f"ntu_{month}.csv.gz")
        with gzip.open(path, "wt", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS)
            for epoch_min, sid, t_, rh, td, ts, p, wkt, wd, flags in by_month[month]:
                s = stats[sid]
                s["n"] += 1
                for fl in flags:
                    s["flags"][fl] += 1
                vals = (t_, rh, ts, p, wkt, wd)
                if prev.get(sid) == vals:
                    s["repeat"] += 1
                prev[sid] = vals
                if rh is not None and rh >= RH_SATURATED:
                    s["sat"] += 1
                if "first" not in s:
                    s["first"] = epoch_min
                s["last_epoch"] = epoch_min
                w.writerow([datetime.fromtimestamp(epoch_min * 60, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            f"NTU:{sid}", fmt(t_, 2), fmt(rh, 1), fmt(td, 2), fmt(ts, 2),
                            fmt(p, 1), fmt(wkt, 1), fmt(wd, 0), "|".join(flags)])
        written[month] = len(by_month[month])

    all_epochs = [s["last_epoch"] for s in stats.values()]
    first_epoch = min(s["first"] for s in stats.values()) if stats else None
    end = datetime.fromtimestamp(max(all_epochs) * 60, UTC) if all_epochs else None
    stations = {}
    for sid, s in sorted(stats.items()):
        _, name, name_en, lat, lon = last_meta[sid]
        last = datetime.fromtimestamp(s["last_epoch"] * 60, UTC)
        first = datetime.fromtimestamp(s["first"] * 60, UTC)
        n = s["n"]
        frac = lambda k: round(s["flags"][k] / n, 4)  # noqa: E731
        stations[f"NTU:{sid}"] = {
            "name": name, "name_en": name_en, "lat": lat, "lon": lon, "elev_m": None,
            "first": first.strftime("%Y-%m-%dT%H:%M:%SZ"), "last": last.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "rows": n,
            "active": bool(end and last >= end - timedelta(days=3)),
            "t_surface_usable": frac("surface_missing") < 0.5,
            "rh_usable": frac("rh_missing") < 0.1,
            "missing_share": {k: frac(k) for k in ("surface_missing", "rh_missing", "pressure_missing")},
            "repeat_share": round(s["repeat"] / n, 4),
            "frozen_suspect": s["repeat"] / n > 0.1,
            "rh_saturated_share": round(s["sat"] / n, 4),
            "dropped_stale": per_station[sid]["dropped_stale"],
        }
    ghosts = sorted(f"NTU:{sid}" for sid in per_station if sid not in stats)
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = {"generated_at": now, "source": SOURCE, "license": "CC BY 4.0",
            "attribution": "Данни: Национално тол управление (bgtoll.bg), CC BY 4.0. Обработка: fogNowcast.",
            "range": [datetime.fromtimestamp(first_epoch * 60, UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      end.strftime("%Y-%m-%dT%H:%M:%SZ")] if stats else None,
            "counts": dict(counts), "rows_written": written, "bad_files": bad_files,
            "ghost_stations_no_fresh_rows": ghosts, "columns": COLUMNS,
            "rules": {"max_fresh_lag_min": 20, "max_future_min": 20, "time": "record time field (UTC)"}}
    with open(os.path.join(out_dir, "ntu_stations.json"), "w", encoding="utf-8") as f:
        json.dump({"generated_at": now, "source": SOURCE, "stations": stations}, f, ensure_ascii=False, indent=1)
    with open(os.path.join(out_dir, "ntu_import_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    return {"meta": meta, "stations": stations}


def main() -> int:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--zip")
    g.add_argument("--download", action="store_true")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    path = a.zip
    if a.download:
        path = os.path.join(tempfile.mkdtemp(), "meteo.zip")
        print(f"downloaded {download(path)} bytes")
    res = import_archive(path, a.out)
    meta, st = res["meta"], res["stations"]
    print("range:", meta["range"])
    print("counts:", meta["counts"])
    print("rows per month:", meta["rows_written"])
    print("bad files:", meta["bad_files"])
    print("ghost stations (no fresh rows):", len(meta["ghost_stations_no_fresh_rows"]))
    act = {k: v for k, v in st.items() if v["active"]}
    print(f"stations with rows: {len(st)}, active: {len(act)}")
    print("t_surface usable:", sum(v["t_surface_usable"] for v in act.values()),
          "| rh usable:", sum(v["rh_usable"] for v in act.values()),
          "| frozen suspects:", [k for k, v in st.items() if v["frozen_suspect"]])
    first_month = sorted(meta["rows_written"])[0] if meta["rows_written"] else None
    if first_month:
        with gzip.open(os.path.join(a.out, f"ntu_{first_month}.csv.gz"), "rt", encoding="utf-8") as f:
            for _ in range(4):
                print(f.readline().rstrip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
