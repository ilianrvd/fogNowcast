"""Historical METAR/SPECI archive of the airports from IEM (branch archive-metar, docs/metar_archive.md).

python -m ingest.metar_archive --archive DIR [--start YYYY-MM-DD] [--end YYYY-MM-DD]

DIR is a checkout of archive-metar (may be empty on the first run). Without --start the run is the daily
top-up: the last 3 days up to yesterday. Every field is parsed from the raw report; IEM fields are used only
as a fallback for visibility. Runs in GitHub Actions; the dev sandbox cannot reach IEM.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from calibration.metar_iem import FOG_TOKENS, FOG_VIS_M, LOW_VIS_M, RETRY_WAIT_S, SM_TO_M, URL, http_text

AIRPORTS = ["LBBG", "LBGO", "LBPD", "LBSF", "LBWN"]
REPORT_TYPES = ("3", "4")  # IEM report_type: both are fetched; IEM files the :30 routine reports as 4, so it is not the type
DEFAULT_START = "2022-01-01"
DAILY_DAYS = 3
PAUSE_S = 2
SOURCE = "METAR/SPECI: Iowa Environmental Mesonet ASOS archive (mesonet.agron.iastate.edu); fogNowcast docs/metar_archive.md"
FIELDS = ["time", "type", "vis_m", "vis_source", "rvr_m", "wx", "fog", "low_vis", "cloud_base_ft", "vv_ft",
          "clouds", "t", "td", "wind_dir", "wind_kt", "gust_kt", "qnh_hpa", "raw"]

_TREND = re.compile(r"\s(?:NOSIG|BECMG|TEMPO|RMK)\b.*$")
_WIND = re.compile(r"^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)$")
_VAR = re.compile(r"^\d{3}V\d{3}$")
_VIS = re.compile(r"^(\d{4})(?:NDV)?$")
_RVR = re.compile(r"^R\d{2}[LRC]?/[PM]?(\d{4})(?:V[PM]?(\d{4}))?(?:FT)?[UDN]?$")
_WX = re.compile(r"^(?:\+|-|VC)?(?:MI|BC|PR|DR|BL|SH|TS|FZ)?(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PO|SQ|FC|SS|DS)*$")
_CLOUD = re.compile(r"^(FEW|SCT|BKN|OVC|///)(\d{3}|///)(CB|TCU|///)?$")
_VV = re.compile(r"^VV(\d{3}|///)$")
_TT = re.compile(r"^(M?\d{2})/(M?\d{2}|//)?$")
_QNH = re.compile(r"^(Q|A)(\d{4})$")
_TAF = re.compile(r"^\S+ \d{6}Z \d{4}/\d{4} ")  # IEM sometimes files a TAF as METAR


def is_taf(raw):
    return bool(_TAF.match((raw or "").strip()))


def _temp(s):
    return None if not s or "/" in s else (-int(s[1:]) if s.startswith("M") else int(s))


def parse_raw(raw):
    """Fields of one METAR/SPECI from the raw report; None where absent or unreadable."""
    out = {k: None for k in ("vis_m", "rvr_m", "cloud_base_ft", "vv_ft", "t", "td", "wind_dir", "wind_kt",
                             "gust_kt", "qnh_hpa")}
    out.update(wx=[], clouds=[])
    body = _TREND.sub("", " " + (raw or "")).split()
    while body and body[0] in ("METAR", "SPECI", "COR"):
        body.pop(0)
    rvr, wind_seen = [], False
    for tok in body[1:]:  # first token is the ICAO
        if tok in ("AUTO", "COR", "NIL") or tok.endswith("Z") and tok[:-1].isdigit():
            continue
        if tok.startswith("RE") or tok.startswith("WS"):
            break  # recent weather and wind shear close the main part
        m = _WIND.match(tok)
        if m and not wind_seen:
            wind_seen = True
            kt = 1.943844 if m.group(4) == "MPS" else 1.0
            out["wind_kt"] = round(int(m.group(2)) * kt)
            out["gust_kt"] = round(int(m.group(3)) * kt) if m.group(3) else None
            calm = out["wind_kt"] == 0
            out["wind_dir"] = None if m.group(1) == "VRB" or calm else int(m.group(1))
            continue
        if _VAR.match(tok):
            continue
        if tok == "CAVOK":
            out["vis_m"] = 9999
            out["clouds"].append("CAVOK")
            continue
        m = _VIS.match(tok)
        if m and out["vis_m"] is None and wind_seen:
            out["vis_m"] = int(m.group(1))
            continue
        m = _RVR.match(tok)
        if m:
            rvr.append(min(int(v) for v in m.groups() if v))
            continue
        m = _CLOUD.match(tok)
        if m:
            out["clouds"].append(tok)
            if m.group(1) in ("BKN", "OVC") and m.group(2).isdigit():
                h = int(m.group(2)) * 100
                out["cloud_base_ft"] = h if out["cloud_base_ft"] is None else min(out["cloud_base_ft"], h)
            continue
        if tok in ("NSC", "NCD", "SKC", "CLR"):
            out["clouds"].append(tok)
            continue
        m = _VV.match(tok)
        if m:
            out["clouds"].append(tok)
            out["vv_ft"] = int(m.group(1)) * 100 if m.group(1).isdigit() else None
            continue
        m = _TT.match(tok)
        if m:
            out["t"], out["td"] = _temp(m.group(1)), _temp(m.group(2))
            continue
        m = _QNH.match(tok)
        if m:
            v = int(m.group(2))
            out["qnh_hpa"] = v if m.group(1) == "Q" else round(v / 100 * 33.8639)
            break  # QNH closes the main part (anything after it is trend or remarks, also misspelt ones)
        if tok not in ("//", "") and _WX.match(tok) and len(tok) >= 2:
            out["wx"].append(tok)
    out["rvr_m"] = min(rvr) if rvr else None
    return out


def report_type(time_iso):
    """METAR at :00 and :30 (routine half-hourly reports of the airports), otherwise SPECI."""
    return "METAR" if time_iso[14:16] in ("00", "30") else "SPECI"


def to_row(iem_row):
    raw = (iem_row.get("metar") or "").strip()
    p = parse_raw(raw)
    vis, src = p["vis_m"], "metar" if p["vis_m"] is not None else None
    if vis is None:
        try:
            vis, src = int(round(float(iem_row.get("vsby")) * SM_TO_M / 100.0)) * 100, "iem"
        except (TypeError, ValueError):
            pass
    t = datetime.strptime(iem_row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return {"time": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "type": report_type(t.strftime("%Y-%m-%dT%H:%M:%SZ")), "vis_m": vis, "vis_source": src,
            "rvr_m": p["rvr_m"], "wx": " ".join(p["wx"]),
            "fog": int(vis is not None and vis < FOG_VIS_M and any(w in FOG_TOKENS for w in p["wx"])),
            "low_vis": int(vis is not None and vis < LOW_VIS_M),
            "cloud_base_ft": p["cloud_base_ft"], "vv_ft": p["vv_ft"], "clouds": " ".join(p["clouds"]),
            "t": p["t"], "td": p["td"], "wind_dir": p["wind_dir"], "wind_kt": p["wind_kt"], "gust_kt": p["gust_kt"],
            "qnh_hpa": p["qnh_hpa"], "raw": raw}


def build_url(station, start, end, report_type):
    """start, end: YYYY-MM-DD (end exclusive)."""
    q = [("station", station), ("data", "all"), ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"),
         ("elev", "no"), ("missing", "M"), ("trace", "T"), ("direct", "no"), ("report_type", report_type)]
    for n, d in (("1", start), ("2", end)):
        q += [(f"year{n}", d[:4]), (f"month{n}", str(int(d[5:7]))), (f"day{n}", str(int(d[8:])))]
    return URL + "?" + urllib.parse.urlencode(q)


def fetch_rows(station, start, end, fetch=http_text, sleep=time.sleep):
    """All METAR and SPECI of one station, one request per report type and calendar year."""
    out = []
    for a, b in year_chunks(start, end):
        for code in REPORT_TYPES:
            url = build_url(station, a, b, code)
            for attempt in range(len(RETRY_WAIT_S) + 1):
                try:
                    text = fetch(url)
                    break
                except (urllib.error.URLError, TimeoutError, OSError):
                    if attempt == len(RETRY_WAIT_S):
                        raise
                    sleep(RETRY_WAIT_S[attempt])
            lines = text.splitlines()
            if not lines or not lines[0].startswith("station,valid"):
                raise RuntimeError(f"unexpected IEM answer for {station}: {text[:200]!r}")
            out += [to_row(r) for r in csv.DictReader(io.StringIO(text)) if not is_taf(r.get("metar"))]
            sleep(PAUSE_S)
    return out


def year_chunks(start, end):
    a = date.fromisoformat(start)
    e = date.fromisoformat(end)
    while a < e:
        b = min(date(a.year + 1, 1, 1), e)
        yield a.isoformat(), b.isoformat()
        a = b


def month_path(root, icao, month):
    return Path(root) / "metar" / icao / f"{month}.csv.gz"


def read_month(path):
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _cell(v):
    return "" if v is None else str(v)


def encode_month(rows):
    """Deterministic gzip bytes (mtime 0), so an unchanged month keeps its sha256."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(FIELDS)
    for r in rows:
        w.writerow([_cell(r[k]) for k in FIELDS])
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode("utf-8"))
    return raw.getvalue()


def merge(old, new):
    """Union by (time, raw), sorted by time; a new copy of the same report replaces the old one.
    The type is derived again from the time, so files written with the IEM report_type are corrected."""
    by = {}
    for r in list(old) + list(new):
        row = {k: _cell(r.get(k)) for k in FIELDS}
        row["type"] = report_type(row["time"])
        by[(row["time"], row["raw"])] = row
    return [by[k] for k in sorted(by)]


def store(root, icao, rows):
    """Merge rows into the month files; returns the months whose file changed."""
    months = {}
    for r in rows:
        months.setdefault(r["time"][:7], []).append(r)
    changed = []
    for month, new in sorted(months.items()):
        path = month_path(root, icao, month)
        data = encode_month(merge(read_month(path), new))
        if not path.exists() or path.read_bytes() != data:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            changed.append(month)
    return changed


def build_manifest(root, period, fetched_at, now):
    airports = {}
    for icao in AIRPORTS:
        months, first, last, total = {}, None, None, 0
        for path in sorted((Path(root) / "metar" / icao).glob("*.csv.gz")):
            rows = read_month(path)
            if not rows:
                continue
            months[path.name[:7]] = {"reports": len(rows), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            first = first or rows[0]["time"]
            last = rows[-1]["time"]
            total += len(rows)
        airports[icao] = {"first_report": first, "last_report": last, "reports": total,
                          "fetched_at": fetched_at.get(icao), "months": months}
    return {"schema_version": 1, "generated_at": now, "source": SOURCE, "status": "research, not for operational use",
            "period": period, "definitions": {"fog": "vis_m < 1000 and FG/FZFG in wx", "low_vis": "vis_m < 5000"},
            "airports": airports}


def main(argv=None, fetch=http_text, sleep=time.sleep, today=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True)
    ap.add_argument("--start", help="first day (backfill); default: daily top-up of the last 3 days")
    ap.add_argument("--end", help="exclusive; default today (UTC)")
    a = ap.parse_args(argv)
    today = today or datetime.now(timezone.utc).date()
    end = a.end or today.isoformat()
    start = a.start or (today - timedelta(days=DAILY_DAYS)).isoformat()
    root = Path(a.archive)
    mpath = root / "manifest.json"
    old = json.loads(mpath.read_text(encoding="utf-8")) if mpath.exists() else {}
    fetched_at = {k: v.get("fetched_at") for k, v in old.get("airports", {}).items()}
    first_day = min(start, (old.get("period") or [start])[0])
    failed = []
    for icao in AIRPORTS:
        try:
            rows = fetch_rows(icao, start, end, fetch=fetch, sleep=sleep)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as e:
            print(f"{icao}: FAILED {e}", file=sys.stderr, flush=True)
            failed.append(icao)
            continue
        changed = store(root, icao, rows)
        fetched_at[icao] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"{icao}: {len(rows)} reports {start}..{end}; changed months: {', '.join(changed) or '-'}", flush=True)
    if len(failed) == len(AIRPORTS):
        print("all airports failed; nothing written", file=sys.stderr)
        return 1
    last_day = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
    period = [first_day, max(last_day, (old.get("period") or [None, last_day])[1])]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = build_manifest(root, period, fetched_at, now)
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
