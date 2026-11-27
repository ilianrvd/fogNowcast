"""Hourly observations from the normalised NTU archive (ntu_YYYY-MM.csv.gz) and sun elevation."""
import csv
import gzip
import math
from datetime import datetime, timedelta, timezone

MAX_OFFSET_MIN = 15  # a record is used for an hour only if it is within +-15 min of HH:00


def parse_time(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _f(x):
    return float(x) if x not in ("", None) else None


def row_to_obs(row: dict) -> dict:
    """Usable values of one archive row; unusable values become None (never zero-filled)."""
    flags = set(filter(None, (row.get("flags") or "").split("|")))
    t = None if "t_missing" in flags else _f(row["t"])
    td = _f(row["td"])
    rh = _f(row["rh"])
    ws = wd = None
    if "wind_missing" not in flags and "wind_zero_both" not in flags:  # zero/zero is ambiguous in the source
        ws, wd = _f(row["wind_kt"]), _f(row["wind_dir"])
    dd = t - td if t is not None and td is not None else None
    return {"t": t, "td": td, "dd": dd, "rh": rh, "ws": ws, "wd": wd, "ts": _f(row.get("t_surface"))}


def slot_obs(paths, station_ids, step_min=60, max_offset_min=MAX_OFFSET_MIN):
    """{(station_id, slot_datetime): obs} for slots every step_min minutes (UTC). Per station and slot the
    record nearest to the slot time (within max_offset_min; ties go to the earlier one) is taken.
    No averaging, so fog is not smoothed."""
    wanted = set(station_ids)
    best = {}
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                sid = row["id"]
                if sid not in wanted:
                    continue
                t = parse_time(row["time"])
                sec = t.timestamp()
                slot_s = int((sec + step_min * 30) // (step_min * 60)) * step_min * 60
                off = abs(sec - slot_s) / 60
                if off > max_offset_min:
                    continue
                key = (sid, datetime.fromtimestamp(slot_s, tz=timezone.utc))
                if key not in best or (off, t) < (best[key][0], best[key][1]):
                    best[key] = (off, t, row)
    return {k: row_to_obs(v[2]) for k, v in best.items()}


def hourly_obs(paths, station_ids):
    """Hourly slots: see slot_obs."""
    return slot_obs(paths, station_ids, step_min=60)


def sun_elevation_deg(t: datetime, lat: float, lon: float) -> float:
    """Approximate solar elevation (NOAA formulas, about 1 degree accuracy); t is UTC."""
    n = t.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1 + (t.hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = t.hour * 60 + t.minute + eqtime + 4 * lon
    ha = math.radians(tst / 4 - 180)
    la = math.radians(lat)
    return math.degrees(math.asin(math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)))
