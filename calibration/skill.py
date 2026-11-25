"""Contingency scores of station-based fog predictors against METAR fog / low visibility."""
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from correction.hourly import sun_elevation_deg

SLOT_MIN = 30
MIN_EVENTS = 10  # a row with fewer events is shown but not ranked
COLD_MONTHS = {10, 11, 12, 1, 2, 3}

PREDICTORS = {
    "rh>=90": lambda o: None if o.get("rh") is None else o["rh"] >= 90,
    "rh>=93": lambda o: None if o.get("rh") is None else o["rh"] >= 93,
    "rh>=95": lambda o: None if o.get("rh") is None else o["rh"] >= 95,
    "rh>=97": lambda o: None if o.get("rh") is None else o["rh"] >= 97,
    "rh>=99.5": lambda o: None if o.get("rh") is None else o["rh"] >= 99.5,
    "dd<=0.5": lambda o: None if o.get("dd") is None else o["dd"] <= 0.5,
    "dd<=1": lambda o: None if o.get("dd") is None else o["dd"] <= 1.0,
    "dd<=2": lambda o: None if o.get("dd") is None else o["dd"] <= 2.0,
    "rh>=95&dd<=1": lambda o: None if o.get("rh") is None or o.get("dd") is None else (o["rh"] >= 95 and o["dd"] <= 1.0),
    "rh>=97&dd<=1": lambda o: None if o.get("rh") is None or o.get("dd") is None else (o["rh"] >= 97 and o["dd"] <= 1.0),
}


def metar_by_slot(reports, step_min=SLOT_MIN):
    """{slot: {"fog": bool, "low_vis": bool, "vis_m": m}}. Each report goes to the nearest slot (within
    half a step); if several fall into one slot the worst (lowest visibility) wins."""
    out = {}
    for r in reports:
        if r["vis_m"] is None:
            continue
        s = r["time"].timestamp()
        slot_s = int((s + step_min * 30) // (step_min * 60)) * step_min * 60
        slot = datetime.fromtimestamp(slot_s, tz=timezone.utc)
        cur = out.get(slot)
        if cur is None or r["vis_m"] < cur["vis_m"]:
            out[slot] = {"fog": r["fog"], "low_vis": r["low_vis"], "vis_m": r["vis_m"]}
        else:  # keep fog / low_vis true if any report of the slot has it
            cur["fog"] = cur["fog"] or r["fog"]
            cur["low_vis"] = cur["low_vis"] or r["low_vis"]
    return out


def subsets(slot, lat, lon):
    yield "all"
    if slot.month in COLD_MONTHS:
        yield "cold"
    if sun_elevation_deg(slot, lat, lon) <= 0:
        yield "night"


def score(counts):
    tp, fp, fn, tn = counts["tp"], counts["fp"], counts["fn"], counts["tn"]
    ev = tp + fn
    return {"n": tp + fp + fn + tn, "events": ev, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "pod": round(tp / ev, 3) if ev else None,
            "far": round(fp / (tp + fp), 3) if tp + fp else None,
            "csi": round(tp / (tp + fp + fn), 3) if tp + fp + fn else None}


def evaluate_airport(metar_slots, obs_by_key, station_ids, lat, lon):
    """Rows {station, predictor, target, subset, ...scores}. station "any" = any of the stations
    meets the predictor (only for airports with more than one station)."""
    counts = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "tn": 0})
    for slot, m in metar_slots.items():
        subs = list(subsets(slot, lat, lon))
        per_station = {sid: obs_by_key.get((sid, slot)) for sid in station_ids}
        for pname, fn_ in PREDICTORS.items():
            votes = {}
            for sid, o in per_station.items():
                if o is not None:
                    v = fn_(o)
                    if v is not None:
                        votes[sid] = v
            if not votes:
                continue
            targets = dict(fog=m["fog"], low_vis=m["low_vis"])
            units = dict(votes)
            if len(station_ids) > 1:
                units["any"] = any(votes.values())
            for unit, pred in units.items():
                for tname, actual in targets.items():
                    key = "tp" if pred and actual else "fp" if pred else "fn" if actual else "tn"
                    for sub in subs:
                        counts[(unit, pname, tname, sub)][key] += 1
    rows = []
    for (unit, pname, tname, sub), c in counts.items():
        rows.append({"station": unit, "predictor": pname, "target": tname, "subset": sub, **score(c)})
    return rows


def top_rows(rows, target, subset, k=3):
    """Best k rows by CSI with at least MIN_EVENTS events."""
    cand = [r for r in rows if r["target"] == target and r["subset"] == subset
            and r["events"] >= MIN_EVENTS and r["csi"] is not None]
    return sorted(cand, key=lambda r: (-r["csi"], r["far"] if r["far"] is not None else 1))[:k]
