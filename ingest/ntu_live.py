"""Live NTU feed -> published observation files and the hourly fallback archive.

Run from GitHub Actions (.github/workflows/ingest-ntu-live.yml), triggered every 15 min by cron-job.org:
    python -m ingest.ntu_live --site site --fallback-archive fb

Writes (see docs/obs_ntu.md):
    site/obs/ntu_latest.json          latest normalised record per station
    site/obs/ntu_recent.json          rolling 24 h of normalised rows (for persistence of model errors)
    site/obs/ntu_fallback_latest.json NTU's own temperature check (/temperaturefallback), refreshed ~hourly
    fb/fallback_YYYY-MM-DD.csv        archive of every hourly fallback state (branch archive-fallback)

/mto/data is requested once per run. /temperaturefallback is requested only when the stored copy is
older than FALLBACK_REFRESH_MIN. If a request fails the previous files are left untouched.
"""
import argparse
import csv
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

from qc.ntu_qc import normalize_record

BASE = "https://bgtoll.bg/index.php/mto/"
UA = "fogNowcast/0.1 (+https://github.com/ilianrvd/fogNowcast)"
SOURCE = "bgtoll.bg/mto/data (Национално тол управление), CC BY 4.0"
UTC = timezone.utc
LATE_MIN = 30               # flag `late` when the record is older than this
OMIT_MIN = 360              # drop records older than this (dead or ghost stations)
RECENT_HOURS = 24
FALLBACK_REFRESH_MIN = 55
FALLBACK_COLUMNS = ["updated_at", "id", "scp", "status", "source", "use_model", "fallback_active",
                    "fallback_reason", "station_temp", "model_temp", "difference", "hits5",
                    "seriousHits", "consecutive5", "consecutive10", "consecutiveOk"]
RECENT_COLUMNS = ["time", "id", "t", "rh", "td", "t_surface", "pressure_hpa", "wind_kt", "wind_dir"]


def http_json(endpoint: str):
    req = urllib.request.Request(BASE + endpoint, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8-sig"))


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def r(v, nd):
    return None if v is None else round(v, nd)


def build_latest(records, now, fallback, elevations):
    best, omitted = {}, 0
    for rec in records:
        try:
            n = normalize_record(rec)
        except Exception:  # noqa: BLE001
            omitted += 1
            continue
        age = (now - n["time"]).total_seconds() / 60
        if age > OMIT_MIN:
            omitted += 1
            continue
        n["age_min"] = round(age)
        sid = n["station_id"]
        if sid not in best or n["time"] > best[sid]["time"]:
            best[sid] = n
    stations = []
    for sid in sorted(best):
        n = best[sid]
        flags = list(n["flags"])
        if n["age_min"] > LATE_MIN:
            flags.append("late")
        fb = (fallback or {}).get("stations", {}).get(str(sid)) or {}
        model_fb = bool(fb.get("fallback_active") or fb.get("use_model"))
        if model_fb:
            flags.append("model_fallback")
        stations.append({
            "id": f"NTU:{sid}", "name": n["name"], "name_en": n["name_en"], "lat": n["lat"], "lon": n["lon"],
            "elev_m": elevations.get(f"NTU:{sid}"), "time": iso(n["time"]), "age_min": n["age_min"],
            "t": r(n["t"], 2), "rh": r(n["rh"], 1), "td": r(n["td"], 2), "t_surface": r(n["t_surface"], 2),
            "pressure_hpa": r(n["pressure_hpa"], 1), "wind_kt": r(n["wind_kt"], 1), "wind_dir": r(n["wind_dir"], 0),
            "ntu_status": fb.get("status"), "ntu_model_t": fb.get("model_temp"),
            "qc": {"excluded": model_fb, "flags": flags},
        })
    newest = max((s["time"] for s in stations), default=None)
    return {"generated_at": iso(now), "source": SOURCE, "obs_time_latest": newest,
            "fallback_updated_at": (fallback or {}).get("updated_at"), "omitted_records": omitted,
            "stations": stations}


def update_recent(prev, latest, now):
    cutoff = iso(now - timedelta(hours=RECENT_HOURS))
    seen = {}
    for row in (prev or {}).get("rows", []):
        if row[0] >= cutoff:
            seen[(row[1], row[0])] = row
    for s in latest["stations"]:
        row = [s["time"], s["id"], s["t"], s["rh"], s["td"], s["t_surface"], s["pressure_hpa"],
               s["wind_kt"], s["wind_dir"]]
        seen[(row[1], row[0])] = row
    rows = sorted(seen.values())
    return {"generated_at": latest["generated_at"], "source": SOURCE, "window_hours": RECENT_HOURS,
            "columns": RECENT_COLUMNS, "rows": rows}


def compact_fallback(raw, now):
    stations, updated = {}, None
    for sid, v in raw.items():
        if not isinstance(v, dict):
            continue
        stations[str(sid)] = {k: v.get(k) for k in (
            "scp", "status", "source", "use_model", "fallback_active", "fallback_reason", "fallback_since_bg",
            "station_temp", "model_temp", "difference", "hits5", "seriousHits", "consecutive5",
            "consecutive10", "consecutiveOk", "updated_at")}
        u = v.get("updated_at")
        if isinstance(u, (int, float)) and (updated is None or u > updated):
            updated = u
    return {"generated_at": iso(now), "fetched_at": iso(now), "source": "bgtoll.bg/mto/temperaturefallback (НТУ)",
            "updated_at": None if updated is None else iso(datetime.fromtimestamp(updated, UTC)),
            "stations": stations}


def archive_fallback(fb_dir, fallback):
    """Append one hourly state to fallback_YYYY-MM-DD.csv (day of its updated_at, UTC). Idempotent."""
    if not fallback.get("updated_at"):
        return False
    day = fallback["updated_at"][:10]
    path = os.path.join(fb_dir, f"fallback_{day}.csv")
    os.makedirs(fb_dir, exist_ok=True)
    exists = os.path.exists(path)
    if exists:
        with open(path, encoding="utf-8") as f:
            if f"{fallback['updated_at']},NTU:" in f.read():
                return False
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(FALLBACK_COLUMNS)
        for sid, v in sorted(fallback["stations"].items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0):
            w.writerow([fallback["updated_at"], f"NTU:{sid}", v["scp"], v["status"], v["source"],
                        int(bool(v["use_model"])), int(bool(v["fallback_active"])), v["fallback_reason"] or "",
                        v["station_temp"], v["model_temp"], v["difference"], v["hits5"], v["seriousHits"],
                        v["consecutive5"], v["consecutive10"], v["consecutiveOk"]])
    return True


def run(site, fb_dir, static_path, fetch=http_json, now=None):
    now = now or datetime.now(UTC)
    elevations = {k: v.get("elev_m") for k, v in (load_json(static_path, {}) or {}).get("stations", {}).items()}
    fb_path = os.path.join(site, "obs", "ntu_fallback_latest.json")
    fallback = load_json(fb_path)
    fetched = (fallback or {}).get("fetched_at")
    need_fb = True
    if fetched:
        age = (now - datetime.strptime(fetched, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)).total_seconds() / 60
        need_fb = age >= FALLBACK_REFRESH_MIN
    status = {"fallback_fetched": False, "fallback_archived": False}
    if need_fb:
        try:
            fallback = compact_fallback(fetch("temperaturefallback"), now)
            write_json(fb_path, fallback)
            status["fallback_fetched"] = True
            status["fallback_archived"] = archive_fallback(fb_dir, fallback)
        except Exception as e:  # noqa: BLE001 - the main feed must not depend on it
            print(f"fallback fetch failed: {type(e).__name__}: {e}", file=sys.stderr)
    data = fetch("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("empty or unexpected /mto/data response; previous files left unchanged")
    latest = build_latest(data, now, fallback, elevations)
    recent = update_recent(load_json(os.path.join(site, "obs", "ntu_recent.json")), latest, now)
    write_json(os.path.join(site, "obs", "ntu_latest.json"), latest)
    write_json(os.path.join(site, "obs", "ntu_recent.json"), recent)
    status.update({"stations": len(latest["stations"]), "omitted": latest["omitted_records"],
                   "obs_time_latest": latest["obs_time_latest"], "recent_rows": len(recent["rows"])})
    return status


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--fallback-archive", required=True)
    ap.add_argument("--static", default="static/ntu_stations.json")
    a = ap.parse_args()
    print(json.dumps(run(a.site, a.fallback_archive, a.static), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
