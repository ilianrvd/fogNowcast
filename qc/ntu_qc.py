"""QC and normalisation rules for NTU road-weather station records (pure functions, no I/O).

Source fields (see docs/obs_ntu.md): air, surface, humidity, pressure, wind (km/h), wind_dir, time (UTC).
In the source a literal 0 in surface, humidity and pressure means "missing".
"""
import math
from datetime import datetime, timezone

UTC = timezone.utc
KMH_PER_KT = 1.852
MAX_FRESH_LAG_MIN = 20      # record older than the snapshot by more than this is stale
MAX_FUTURE_MIN = 20         # record newer than the snapshot by more than this is dropped
MAGNUS_A, MAGNUS_B = 17.625, 243.04  # Alduchov-Eskridge coefficients (degC)
RH_SATURATED = 99.5


def dew_point(t_c: float, rh: float) -> float:
    gamma = math.log(rh / 100.0) + MAGNUS_A * t_c / (MAGNUS_B + t_c)
    return MAGNUS_B * gamma / (MAGNUS_A - gamma)


def kmh_to_kt(v: float) -> float:
    return v / KMH_PER_KT


def classify_lag(lag_min: float) -> str:
    """lag = snapshot time - record time, in minutes."""
    if lag_min > MAX_FRESH_LAG_MIN:
        return "stale"
    if lag_min < -MAX_FUTURE_MIN:
        return "future"
    return "ok"


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None


def normalize_record(rec: dict) -> dict:
    """Normalise one source record. Raises KeyError/ValueError on a record without id or time."""
    sid = int(rec["station_id"])
    time = datetime.strptime(rec["time"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    flags = []

    t = _num(rec.get("air"))
    if t is None:
        flags.append("t_missing")

    ts = _num(rec.get("surface"))
    if ts is None or ts == 0:
        ts = None
        flags.append("surface_missing")
    elif ts < -40 or ts > 80 or (t is not None and abs(ts - t) > 30):
        flags.append("surface_suspect")

    rh = _num(rec.get("humidity"))
    if rh is None or rh == 0:
        rh = None
        flags.append("rh_missing")
    elif rh > 100:
        rh = 100.0
        flags.append("rh_over_100")

    p = _num(rec.get("pressure"))
    if p is None or p == 0:
        p = None
        flags.append("pressure_missing")

    w = _num(rec.get("wind"))
    wd = _num(rec.get("wind_dir"))
    if w is None:
        flags.append("wind_missing")
    elif w == 0 and (wd is None or wd == 0):
        flags.append("wind_zero_both")  # calm or missing, ambiguous in the source
        wd = None
    elif wd is not None:
        wd = wd % 360.0

    td = None
    if t is not None and rh is not None:
        td = dew_point(t, rh)
    return {
        "station_id": sid, "time": time,
        "name": rec.get("name"), "name_en": rec.get("nameENG"),
        "lat": _num(rec.get("lat")), "lon": _num(rec.get("lon")),
        "t": t, "rh": rh, "td": td, "t_surface": ts, "pressure_hpa": p,
        "wind_kt": None if w is None else kmh_to_kt(w), "wind_dir": wd,
        "flags": flags,
    }
