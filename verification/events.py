"""Scores for the moments that matter: fog onset, fog clearing, and fog episodes (docs/verification.md).

Persistence (the METAR at t0 stays) is right most of the time because weather is mostly persistent, but it
never forecasts a change: it has POD 0 for every onset and every clearing. These scores look only at the
changes, where a regression and a numerical model are expected to add value.

A case is a dict: point, t0 (datetime), lead (h), day (date), p (probability), dec (bool decision),
y (fog at t0 + lead), y0 (fog at t0).
"""
from collections import defaultdict
from datetime import timedelta

from . import core

EPISODE_GAP_H = 1.5      # fog reports closer than this belong to one episode
CLEAR_BEFORE_H = 2.0     # an onset counts only after at least this long without fog
NIGHT_SHIFT_H = 9        # night = 18-09 UTC, keyed by the date of (t - 9 h)


def _block(sel, event, pred, pers_pred):
    ys = [1.0 if event(c) else 0.0 for c in sel]
    dec = [pred(c) for c in sel]
    prob = [c["p"] if c["p"] is not None else (1.0 if pred(c) else 0.0) for c in sel]
    pers = [1.0 if pers_pred(c) else 0.0 for c in sel]
    out = {"n": len(sel), "events": int(sum(ys)), **core.contingency(dec, ys, 0.5)}
    if sel:
        out.update(core.bootstrap_fog([c["day"] for c in sel], list(zip(dec, prob)), pers, ys) or {})
    return out


def transition_scores(cases):
    """By lead 1-6: onset (clear at t0) and clearing (fog at t0). Persistence never forecasts either."""
    out = []
    for lead in range(1, 7):
        sel = [c for c in cases if c["lead"] == lead]
        onset = [c for c in sel if not c["y0"]]
        clear = [c for c in sel if c["y0"]]
        row = {"lead_h": lead,
               # onset: event = fog arrives; forecast = dec; persistence says "no fog"
               "onset": _block(onset, lambda c: c["y"], lambda c: c["dec"], lambda c: False),
               # clearing: event = fog gone; forecast "clear" = not dec; persistence says "fog stays"
               "clearing": _block([dict(c, p=1.0 - c["p"]) if c["p"] is not None else c for c in clear],
                                  lambda c: not c["y"], lambda c: not c["dec"], lambda c: False)}
        if onset:
            ys = [1.0 if c["y"] else 0.0 for c in onset]
            row["onset"]["brier"] = round(core.brier([c["p"] for c in onset], ys), 4)
            row["onset"]["brier_persistence"] = round(core.brier([0.0] * len(ys), ys), 4)
        out.append(row)
    return out


def episodes(fog_series):
    """{point: [(onset, end)]} from {point: [(time, fog_bool)]}; only onsets after CLEAR_BEFORE_H without fog."""
    out = {}
    for pt, series in fog_series.items():
        series = sorted(series)
        eps, start, last_fog, last_clear_obs = [], None, None, None
        prev_fog_end = None
        for t, fog in series:
            if fog:
                if start is None or (t - last_fog) > timedelta(hours=EPISODE_GAP_H):
                    if start is not None:
                        eps.append((start, last_fog, ok))
                    ok = prev_fog_end is None or (t - prev_fog_end) >= timedelta(hours=CLEAR_BEFORE_H)
                    ok = ok and last_clear_obs is not None and (t - last_clear_obs) <= timedelta(hours=1)
                    start = t
                last_fog = t
                prev_fog_end = t
            else:
                last_clear_obs = t
        if start is not None:
            eps.append((start, last_fog, ok))
        out[pt] = [(a, b) for a, b, k in eps if k]
    return out


def episode_scores(cases, fog_series):
    """Per fog episode: was fog forecast (dec) for the onset time (+-1 h) from a clear t0, and how early?
    And false-alarm nights: nights without any fog with at least one forecast of fog from a clear t0."""
    eps = episodes(fog_series)
    by_pt = defaultdict(list)
    for c in cases:
        if not c["y0"] and c["dec"]:
            by_pt[c["point"]].append(c)
    warned_lead = []
    for pt, lst in eps.items():
        warns = by_pt.get(pt, [])
        for onset, _end in lst:
            best = 0
            for c in warns:
                valid = c["t0"] + timedelta(hours=c["lead"])
                if abs((valid - onset).total_seconds()) <= 3600 and c["t0"] < onset:
                    best = max(best, round((onset - c["t0"]).total_seconds() / 3600))
            warned_lead.append(best)
    n = len(warned_lead)
    res = {"episodes": n,
           "warned_any": sum(1 for x in warned_lead if x >= 1),
           "warned_2h": sum(1 for x in warned_lead if x >= 2),
           "warned_3h": sum(1 for x in warned_lead if x >= 3),
           "missed": sum(1 for x in warned_lead if x == 0)}
    for k in ("warned_any", "warned_2h", "warned_3h"):
        res[k + "_share"] = round(res[k] / n, 3) if n else None
    # nights
    night = lambda t: (t - timedelta(hours=NIGHT_SHIFT_H)).date()
    in_night = lambda t: t.hour >= 18 or t.hour < NIGHT_SHIFT_H
    fog_nights, all_nights, alarm_nights = set(), set(), set()
    for pt, series in fog_series.items():
        for t, fog in series:
            if in_night(t):
                all_nights.add((pt, night(t)))
                if fog:
                    fog_nights.add((pt, night(t)))
    for c in cases:
        valid = c["t0"] + timedelta(hours=c["lead"])
        if c["dec"] and not c["y0"] and in_night(valid):
            alarm_nights.add((c["point"], night(valid)))
    clear_nights = all_nights - fog_nights
    res["clear_nights"] = len(clear_nights)
    res["false_alarm_nights"] = len(alarm_nights & clear_nights)
    res["false_alarm_night_share"] = round(len(alarm_nights & clear_nights) / len(clear_nights), 3) if clear_nights else None
    return res


def report_lines(ev, title):
    L = [f"#### {title}: промени във времето", "",
         "Устойчивостта никога не прогнозира промяна: при образуване и при разсейване тя има POD 0. "
         "ИД = 95 % интервал по дни на CSI (над 0 = методът хваща промени).", "",
         "| срок | образуване: случаи | POD | FAR | CSI | ИД CSI | разсейване: случаи | POD | FAR | CSI |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in ev["transitions"]:
        o, c = r["onset"], r["clearing"]
        L.append(f"| {r['lead_h']} | {o['events']} | {o['pod']} | {o['far']} | {o['csi']} | {o.get('csi_diff_ci95')} | "
                 f"{c['events']} | {c['pod']} | {c['far']} | {c['csi']} |")
    e = ev["episodes"]
    L += ["", f"Епизоди с мъгла (начало след поне {CLEAR_BEFORE_H:g} ч без мъгла): {e['episodes']}; "
          f"предупредени поне 1 ч по-рано: {e['warned_any']} ({e['warned_any_share']}), поне 2 ч: {e['warned_2h']} "
          f"({e['warned_2h_share']}), поне 3 ч: {e['warned_3h']} ({e['warned_3h_share']}); изпуснати: {e['missed']}. "
          f"Нощи без мъгла с фалшива тревога: {e['false_alarm_nights']} от {e['clear_nights']} "
          f"({e['false_alarm_night_share']}).", ""]
    return L
