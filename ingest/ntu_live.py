"""Live NTU feed -> published observation files and the hourly fallback archive.

Run from GitHub Actions (.github/workflows/ingest-ntu-live.yml), triggered every 15 min by cron-job.org:
    python -m ingest.ntu_live --site site --fallback-archive fb

Writes (see docs/obs_ntu.md):
    site/obs/ntu_latest.json          latest normalised record per station
    site/obs/ntu_recent.json          rolling 24 h of normalised rows (for persistence of model errors)
    site/obs/ntu_fallback_latest.json NTU's own temperature check (/temperaturefallback), refreshed ~hourly
    fb/fallback_YYYY-MM-DD.csv        archive of every fallback state, ~every 15 min (branch archive-fallback)
    live/ntu_live_YYYY-MM.csv.gz      archive of every new live record (branch archive-live), append-only

/mto/data is requested once per run. /temperaturefallback is requested only when the stored copy is
older than FALLBACK_REFRESH_MIN. If a request fails the previous files are left untouched.

Operational guards (docs/obs_ntu.md):
- no request at all if the previous request was less than MIN_REQUEST_INTERVAL_MIN ago (NTU rule: at most
  one request per 15 min; protects against manual or duplicate runs). The time of every request attempt is
  kept in site/obs/ntu_fetch_state.json, which is published even when the run fails;
- the run fails, and the published files stay as they were, if fewer than MIN_STATIONS stations are usable
  (format change, mass outage) - a good file is never replaced by an empty one;
- NTU's model-fallback state older than FALLBACK_MAX_AGE_MIN, or missing for a station, is "unknown";
- a temperature that has not changed for FROZEN_T_HOURS is flagged as a stuck sensor and excluded.
"""
import argparse
import csv
import gzip
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

from ingest.ntu_archive import COLUMNS as ARCHIVE_COLUMNS, fmt
from qc.ntu_qc import normalize_record

BASE = "https://bgtoll.bg/index.php/mto/"
UA = "fogNowcast/0.1 (+https://github.com/ilianrvd/fogNowcast)"
SOURCE = "bgtoll.bg/mto/data (Национално тол управление), CC BY 4.0"
CADENCE_MIN = 15
DOCS = "https://github.com/ilianrvd/fogNowcast/blob/main/docs/obs_ntu.md"
SITE = "https://ilianrvd.github.io/fogNowcast/"
# read by core.fog_points (portal manifest.md, consumes[]); the model file is regenerated every 6 h, METAR every 30 min
CONSUMES = [{"project": "multiconvect", "path": "latest_points.json", "max_age_min": 480},
            {"project": "nowcasting-public", "path": "obs/metar_latest.json", "max_age_min": 60}]


def with_header(schema, d):
    """Portal header first (schema, generated_at, source, run, cadence_min; see the portal's docs/header.md),
    then the file's own fields. run is null: these are observations, not a model run."""
    out = {"schema": schema, "generated_at": d["generated_at"], "source": d["source"], "run": None,
           "cadence_min": CADENCE_MIN}
    out.update({k: v for k, v in d.items() if k not in out})
    return out


def build_manifest(now, latest, recent, fallback, previous=None):
    """manifest.json for the portal (portal.manifest/1.0). Written only after a successful run."""
    rows = recent.get("rows") or []
    products = [
        {"id": "ntu_latest", "path": "obs/ntu_latest.json", "format": "json", "schema": "fognowcast.ntu_latest/1.0",
         "doc": DOCS + "#obsntu_latestjson", "cadence_min": CADENCE_MIN, "generated_at": latest["generated_at"],
         "data_time": latest.get("obs_time_latest")},
        {"id": "ntu_recent", "path": "obs/ntu_recent.json", "format": "json", "schema": "fognowcast.ntu_recent/1.0",
         "doc": DOCS + "#obsntu_recentjson", "cadence_min": CADENCE_MIN, "generated_at": recent["generated_at"],
         "data_time": max(r[0] for r in rows) if rows else None},
    ]
    if fallback and fallback.get("generated_at"):
        products.append(
            {"id": "ntu_fallback", "path": "obs/ntu_fallback_latest.json", "format": "json",
             "schema": "fognowcast.ntu_fallback/1.0", "doc": DOCS + "#obsntu_fallback_latestjson",
             "cadence_min": CADENCE_MIN, "generated_at": fallback["generated_at"], "data_time": fallback.get("updated_at")})
    # fog_points is added by core.fog_points (the step after this one); keep the last entry, so that a failed fog step
    # does not make the product disappear from the portal (its generated_at then shows how old it is)
    products += [p for p in (previous or {}).get("products", []) if p.get("id") in ("fog_points", "fog_polygons")]
    return {"schema": "portal.manifest/1.0", "generated_at": iso(now), "source": "fogNowcast",
            "id": "fognowcast", "name": {"bg": "fogNowcast", "en": "fogNowcast"},
            "description": {"bg": "Наблюдения от пътните станции на НТУ и некалибрирана прогноза по точки (модел + корекция от наблюдението)",
                            "en": "NTU road weather stations and an uncalibrated point forecast (model + observation correction)"},
            "url": SITE, "repo": "https://github.com/ilianrvd/fogNowcast", "status": "experimental",
            "cadence_min": CADENCE_MIN, "run": None,
            "embed": {"url": "index.html?embed=1", "min_height_px": 800, "gated": False}, "products": products,
            "consumes": CONSUMES}
UTC = timezone.utc
LATE_MIN = 30               # flag `late` when the record is older than this
OMIT_MIN = 360              # drop records older than this (dead or ghost stations)
RECENT_HOURS = 24
FALLBACK_REFRESH_MIN = 10   # the source updates every 15 min (seen at :00:01 and :15:01), so ask on every run
FALLBACK_COLUMNS = ["updated_at", "id", "scp", "status", "source", "use_model", "fallback_active",
                    "fallback_reason", "station_temp", "model_temp", "difference", "hits5",
                    "seriousHits", "consecutive5", "consecutive10", "consecutiveOk"]
MIN_REQUEST_INTERVAL_MIN = 14  # cron runs every 15 min; start times jitter by tens of seconds
MIN_STATIONS = 20             # ~95 stations report normally
FALLBACK_MAX_AGE_MIN = 60     # the source updates every 15 min
FROZEN_T_HOURS = 3            # t identical for this long (and at least FROZEN_MIN_ROWS rows) = stuck sensor
FROZEN_RH_HOURS = 6           # rh identical for this long = flag only (persistent fog can look the same)
FROZEN_MIN_ROWS = 10
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


def fallback_age_min(fallback, now):
    u = (fallback or {}).get("updated_at")
    if not u:
        return None
    return (now - datetime.strptime(u, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)).total_seconds() / 60


def build_latest(records, now, fallback, elevations):
    fb_age = fallback_age_min(fallback, now)
    fallback_fresh = fallback if fb_age is not None and fb_age <= FALLBACK_MAX_AGE_MIN else None
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
        fb = (fallback_fresh or {}).get("stations", {}).get(str(sid))
        model_fb = bool(fb and (fb.get("fallback_active") or fb.get("use_model")))
        if model_fb:
            flags.append("model_fallback")
        elif fb is None:
            flags.append("fallback_unknown")  # NTU may be sending model values; not excluded, but not confirmed
        fb = fb or {}
        stations.append({
            "id": f"NTU:{sid}", "name": n["name"], "name_en": n["name_en"], "lat": n["lat"], "lon": n["lon"],
            "elev_m": elevations.get(f"NTU:{sid}"), "time": iso(n["time"]), "age_min": n["age_min"],
            "t": r(n["t"], 2), "rh": r(n["rh"], 1), "td": r(n["td"], 2), "t_surface": r(n["t_surface"], 2),
            "pressure_hpa": r(n["pressure_hpa"], 1), "wind_kt": r(n["wind_kt"], 1), "wind_dir": r(n["wind_dir"], 0),
            "ntu_status": fb.get("status"), "ntu_model_t": fb.get("model_temp"),
            "qc": {"excluded": model_fb, "flags": flags},
        })
    newest = max((s["time"] for s in stations), default=None)
    return with_header("fognowcast.ntu_latest/1.0", {"generated_at": iso(now), "source": SOURCE, "obs_time_latest": newest,
            "fallback_updated_at": (fallback or {}).get("updated_at"),
            "fallback_used": fallback_fresh is not None, "omitted_records": omitted,
            "stations": stations})


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
    return with_header("fognowcast.ntu_recent/1.0", {"generated_at": latest["generated_at"], "source": SOURCE, "window_hours": RECENT_HOURS,
            "columns": RECENT_COLUMNS, "rows": rows})


def flag_frozen(latest, recent, now):
    """Add t_frozen (and exclude) / rh_constant flags from the rolling 24 h rows."""
    cols = recent["columns"]
    it, irh = cols.index("t"), cols.index("rh")
    by = {}
    for row in recent["rows"]:
        by.setdefault(row[1], []).append(row)
    for s in latest["stations"]:
        rows = by.get(s["id"], [])
        for col, hours, flag in ((it, FROZEN_T_HOURS, "t_frozen"), (irh, FROZEN_RH_HOURS, "rh_constant")):
            cut = iso(now - timedelta(hours=hours))
            win = [r_[col] for r_ in rows if r_[0] >= cut and r_[col] is not None]
            span_ok = rows and min(r_[0] for r_ in rows) <= cut  # history covers the whole window
            if span_ok and len(win) >= FROZEN_MIN_ROWS and len(set(win)) == 1:
                s["qc"]["flags"].append(flag)
                if flag == "t_frozen":
                    s["qc"]["excluded"] = True


ARCHIVE_SKIP_FLAGS = {"late"}  # about publication, not about the measurement


def archive_live(live_dir, latest):
    """Append records not yet archived to ntu_live_YYYY-MM.csv.gz (month of the record time, UTC).
    Same columns as the NTU archive (docs/archive_ntu.md). Each call adds one gzip member; existing data
    is never rewritten. Key: (id, time). Returns the number of rows added."""
    os.makedirs(live_dir, exist_ok=True)
    by_month = {}
    for s in latest["stations"]:
        by_month.setdefault(s["time"][:7], []).append(s)
    added = 0
    for month, stations in sorted(by_month.items()):
        path = os.path.join(live_dir, f"ntu_live_{month}.csv.gz")
        seen, exists = set(), os.path.exists(path)
        if exists:
            with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
                for row in csv.reader(f):
                    if row and row[0] != "time":
                        seen.add((row[1], row[0]))
        new = [s for s in stations if (s["id"], s["time"]) not in seen]
        if not new:
            continue
        with gzip.open(path, "at", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow(ARCHIVE_COLUMNS)
            for s in sorted(new, key=lambda x: (x["time"], int(x["id"].split(":")[1]))):
                flags = [x for x in s["qc"]["flags"] if x not in ARCHIVE_SKIP_FLAGS]
                w.writerow([s["time"], s["id"], fmt(s["t"], 2), fmt(s["rh"], 1), fmt(s["td"], 2),
                            fmt(s["t_surface"], 2), fmt(s["pressure_hpa"], 1), fmt(s["wind_kt"], 1),
                            fmt(s["wind_dir"], 0), "|".join(flags)])
        added += len(new)
    return added


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
    return with_header("fognowcast.ntu_fallback/1.0", {"generated_at": iso(now), "fetched_at": iso(now), "source": "bgtoll.bg/mto/temperaturefallback (НТУ)",
            "updated_at": None if updated is None else iso(datetime.fromtimestamp(updated, UTC)),
            "stations": stations})


def archive_fallback(fb_dir, fallback):
    """Append one state to fallback_YYYY-MM-DD.csv (day of its updated_at, UTC). Idempotent."""
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


def run(site, fb_dir, static_path, fetch=http_json, now=None, live_dir=None):
    now = now or datetime.now(UTC)
    state_path = os.path.join(site, "obs", "ntu_fetch_state.json")
    last = (load_json(state_path) or {}).get("last_request_at")
    if last:
        since = (now - datetime.strptime(last, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)).total_seconds() / 60
        if since < MIN_REQUEST_INTERVAL_MIN:
            return {"skipped": True, "reason": f"last request {since:.1f} min ago (< {MIN_REQUEST_INTERVAL_MIN})"}
    write_json(state_path, with_header("fognowcast.ntu_fetch_state/1.0", {
        "generated_at": iso(now), "source": "fogNowcast ingest.ntu_live", "last_request_at": iso(now)}))
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
    if len(latest["stations"]) < MIN_STATIONS:
        raise RuntimeError(f"only {len(latest['stations'])} usable stations (< {MIN_STATIONS}), "
                           f"{latest['omitted_records']} records omitted; previous files left unchanged")
    recent = update_recent(load_json(os.path.join(site, "obs", "ntu_recent.json")), latest, now)
    flag_frozen(latest, recent, now)
    write_json(os.path.join(site, "obs", "ntu_latest.json"), latest)
    write_json(os.path.join(site, "obs", "ntu_recent.json"), recent)
    write_json(os.path.join(site, "manifest.json"),
               build_manifest(now, latest, recent, fallback, load_json(os.path.join(site, "manifest.json"))))
    if live_dir:
        try:
            status["live_archived"] = archive_live(live_dir, latest)
        except Exception as e:  # noqa: BLE001 - the published files must not depend on the archive
            status["live_archived"] = None
            print(f"live archive failed: {type(e).__name__}: {e}", file=sys.stderr)
    status.update({"stations": len(latest["stations"]), "omitted": latest["omitted_records"],
                   "obs_time_latest": latest["obs_time_latest"], "recent_rows": len(recent["rows"])})
    return status


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--site", required=True)
    ap.add_argument("--fallback-archive", required=True)
    ap.add_argument("--static", default="static/ntu_stations.json")
    ap.add_argument("--live-archive", default=None)
    a = ap.parse_args()
    print(json.dumps(run(a.site, a.fallback_archive, a.static, live_dir=a.live_archive), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
