"""One-off profile of the whole NTU open-data archive (Dropbox). Read-only, nothing saved to the repo.

Run only from GitHub Actions (.github/workflows/profile-ntu-archive.yml).
Downloads the folder zip once (~230 MB) to a temp file, walks every snapshot and aggregates
statistics that decide the QC rules: cadence and gaps, record lag, duplicates, zero-as-missing
rates, value ranges, coordinate changes. Writes a JSON report (workflow artifact) and prints a summary.
"""
import io
import json
import os
import sys
import tempfile
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

FOLDER = ("https://www.dropbox.com/scl/fo/3v3asm04vcf3oahhgb39w/AJ8-3EDvwDf7d3a6OGir1wo"
          "?rlkey=i95zfngdxye3lj6zwrk97cgg5&st=4pyajkii&dl=1")
UA = "fogNowcast-probe/0.1 (+https://github.com/ilianrvd/fogNowcast)"
SOFIA = ZoneInfo("Europe/Sofia")
UTC = timezone.utc
LAG_BINS = [(0, 20, "<=20min"), (20, 60, "20-60min"), (60, 720, "1-12h"), (720, 10**9, ">12h")]


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


def file_time_utc(member: str) -> datetime:
    # stationsData_YYYYMMDD_HHMM.json, local time (Europe/Sofia)
    stem = member.rsplit("/", 1)[-1][len("stationsData_"):-len(".json")]
    local = datetime.strptime(stem, "%Y%m%d_%H%M").replace(tzinfo=SOFIA)
    return local.astimezone(UTC)


def new_station():
    return {"n": 0, "unique_times": set(), "stale_30": 0, "stale_720": 0, "zero_surface": 0,
            "zero_humidity": 0, "zero_pressure": 0, "zero_wind_and_dir": 0, "zero_air": 0,
            "rh_ge_995": 0, "rh_gt_100": 0, "air_out_of_range": 0,
            "names": Counter(), "coords": Counter(), "first": None, "last": None}


def main() -> int:
    path = os.path.join(tempfile.mkdtemp(), "meteo.zip")
    print(f"downloaded {download(path)} bytes")
    outer = zipfile.ZipFile(path)
    days = sorted((i for i in outer.infolist() if not i.is_dir() and i.filename.endswith(".zip")),
                  key=lambda i: i.filename.rsplit("/", 1)[-1])
    stations = defaultdict(new_station)
    lag_hist = Counter()
    per_day = {}
    member_count_hist = Counter()
    name_offset = Counter()  # file_time_utc minute-of-hour offsets (cadence)
    vals = defaultdict(list)  # sampled values for range stats
    total_records = 0
    parse_errors = Counter()

    for info in days:
        day_name = info.filename.rsplit("/", 1)[-1][len("meteoData_"):-len(".zip")]
        inner = zipfile.ZipFile(io.BytesIO(outer.read(info)))
        members = sorted(n for n in inner.namelist() if n.endswith(".json"))
        member_count_hist[len(members)] += 1
        day_ids, day_records = set(), 0
        times_utc = []
        for m in members:
            try:
                ft = file_time_utc(m)
                data = json.loads(inner.read(m).decode("utf-8-sig"))
            except Exception as e:  # noqa: BLE001
                parse_errors[type(e).__name__] += 1
                continue
            times_utc.append(ft)
            name_offset[ft.minute % 15] += 1
            if not isinstance(data, list):
                parse_errors["not_list"] += 1
                continue
            for r in data:
                try:
                    sid = int(r["station_id"])
                    rt = datetime.strptime(r["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
                except Exception as e:  # noqa: BLE001
                    parse_errors["record_" + type(e).__name__] += 1
                    continue
                total_records += 1
                day_records += 1
                day_ids.add(sid)
                s = stations[sid]
                s["n"] += 1
                s["unique_times"].add(int(rt.timestamp()) // 60)
                lag = (ft - rt).total_seconds() / 60
                for lo, hi, label in LAG_BINS:
                    if lo <= lag < hi:
                        lag_hist[label] += 1
                        break
                else:
                    lag_hist["negative(file before record)"] += 1
                if lag > 30:
                    s["stale_30"] += 1
                if lag > 720:
                    s["stale_720"] += 1
                s["names"][(r.get("name"), r.get("nameENG"))] += 1
                s["coords"][(round(r.get("lat", 0), 6), round(r.get("lon", 0), 6))] += 1
                if s["first"] is None or rt < s["first"]:
                    s["first"] = rt
                if s["last"] is None or rt > s["last"]:
                    s["last"] = rt
                air, sur, hum = r.get("air"), r.get("surface"), r.get("humidity")
                pr, w, wd = r.get("pressure"), r.get("wind"), r.get("wind_dir")
                s["zero_surface"] += sur == 0
                s["zero_humidity"] += hum == 0
                s["zero_pressure"] += pr == 0
                s["zero_wind_and_dir"] += (w == 0 and wd == 0)
                s["zero_air"] += air == 0
                if isinstance(hum, (int, float)):
                    s["rh_ge_995"] += hum >= 99.5
                    s["rh_gt_100"] += hum > 100
                if isinstance(air, (int, float)):
                    s["air_out_of_range"] += not (-40 <= air <= 50) and air != 0
                if lag <= 30:
                    for k, v in (("air", air), ("surface", sur), ("humidity", hum),
                                 ("pressure", pr), ("wind", w)):
                        if isinstance(v, (int, float)) and v != 0 and len(vals[k]) < 400000:
                            vals[k].append(v)
        per_day[day_name] = {"members": len(members), "records": day_records, "stations": len(day_ids),
                             "first_file_utc": min(times_utc).isoformat() if times_utc else None,
                             "last_file_utc": max(times_utc).isoformat() if times_utc else None}

    # missing dates
    have = sorted(datetime.strptime(d, "%Y-%m-%d").date() for d in per_day)
    missing = []
    d = have[0]
    while d <= have[-1]:
        if d.isoformat() not in per_day:
            missing.append(d.isoformat())
        d += timedelta(days=1)

    def pct(arr, p):
        arr = sorted(arr)
        return round(arr[min(len(arr) - 1, int(p / 100 * len(arr)))], 2) if arr else None

    ranges = {k: {p: pct(v, p) for p in (0.1, 1, 50, 99, 99.9)} | {"min": min(v), "max": max(v), "n": len(v)}
              for k, v in vals.items() if v}
    st_report = {}
    for sid, s in sorted(stations.items()):
        n = s["n"]
        st_report[sid] = {
            "name": s["names"].most_common(1)[0][0], "name_variants": len(s["names"]),
            "coord_variants": len(s["coords"]), "coord": s["coords"].most_common(1)[0][0],
            "n": n, "unique_times": len(s["unique_times"]),
            "first": s["first"].isoformat(), "last": s["last"].isoformat(),
            **{k: round(s[k] / n, 4) for k in ("stale_30", "stale_720", "zero_surface", "zero_humidity",
                                               "zero_pressure", "zero_wind_and_dir", "zero_air",
                                               "rh_ge_995", "rh_gt_100", "air_out_of_range")}}
    report = {"days": len(per_day), "date_range": [have[0].isoformat(), have[-1].isoformat()],
              "missing_dates": missing, "total_records": total_records,
              "members_per_day_hist": dict(sorted(member_count_hist.items())),
              "file_minute_mod15_hist": dict(name_offset), "lag_hist": dict(lag_hist),
              "value_ranges_fresh_nonzero": ranges, "parse_errors": dict(parse_errors),
              "stations": st_report, "per_day": per_day}
    out = os.environ.get("REPORT_PATH", "profile.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1, default=str)

    print(f"days={report['days']} range={report['date_range']} records={total_records}")
    print("missing dates:", missing)
    print("members per day (count of days):", report["members_per_day_hist"])
    print("file minute mod 15:", report["file_minute_mod15_hist"])
    print("record lag histogram:", report["lag_hist"])
    print("parse errors:", report["parse_errors"])
    print("value ranges (fresh, non-zero):")
    for k, v in ranges.items():
        print(f"  {k}: {v}")
    print(f"stations: {len(st_report)}")
    hdr = "id   name                         n   uniq  stale30 zeroSurf zeroRH zeroP  rh>=99.5 coordVar nameVar first..last"
    print(hdr)
    for sid, s in st_report.items():
        print(f"{sid:<4} {str(s['name'][0])[:24]:<24} {s['n']:>7} {s['unique_times']:>6} "
              f"{s['stale_30']:>7} {s['zero_surface']:>8} {s['zero_humidity']:>6} {s['zero_pressure']:>5} "
              f"{s['rh_ge_995']:>8} {s['coord_variants']:>7} {s['name_variants']:>7} "
              f"{s['first'][:10]}..{s['last'][:10]}")
    for special in ("2026-03-28", "2026-03-29", "2026-03-30", "2026-10-24", "2026-10-25", "2026-10-26"):
        print("DST check", special, per_day.get(special))
    os.remove(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
