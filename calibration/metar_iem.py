"""Historical METAR/SPECI of the airports from the IEM ASOS archive (Iowa Environmental Mesonet).

Not available from the dev sandbox; runs in GitHub Actions. Visibility is parsed from the raw report
in metres (IEM `vsby` is in statute miles, rounded, so 1000 m would come out as 998 m).
"""
import csv
import io
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
UA = "fogNowcast/0.1 (+https://github.com/ilianrvd/fogNowcast)"
SM_TO_M = 1609.344
RETRY_WAIT_S = (5, 15, 45)
FOG_TOKENS = {"FG", "FZFG"}  # BCFG, MIFG, PRFG, VCFG are not fog over the runway
FOG_VIS_M = 1000
LOW_VIS_M = 5000
_VIS_RE = re.compile(r"\b(?:\d{3}|VRB)\d{2}(?:G\d{2,3})?KT(?: \d{3}V\d{3})? (CAVOK|\d{4})\b")


def http_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def build_url(station, start, end):
    """start, end: YYYY-MM-DD (end exclusive). Report types 3 (routine) and 4 (special)."""
    q = [("station", station), ("data", "all"), ("tz", "Etc/UTC"), ("format", "onlycomma"), ("latlon", "no"),
         ("elev", "no"), ("missing", "M"), ("trace", "T"), ("direct", "no"), ("report_type", "3"),
         ("report_type", "4")]
    for n, d in (("1", start), ("2", end)):
        q += [(f"year{n}", d[:4]), (f"month{n}", str(int(d[5:7]))), (f"day{n}", str(int(d[8:])))]
    return URL + "?" + urllib.parse.urlencode(q)


def parse_vis_m(raw, vsby_sm):
    """(visibility in m, source). Raw METAR first; otherwise IEM vsby rounded to 100 m."""
    m = _VIS_RE.search(raw or "")
    if m:
        return (9999 if m.group(1) == "CAVOK" else int(m.group(1))), "metar"
    try:
        return int(round(float(vsby_sm) * SM_TO_M / 100.0)) * 100, "iem"
    except (TypeError, ValueError):
        return None, None


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_report(row):
    raw = row.get("metar") or ""
    vis, src = parse_vis_m(raw, row.get("vsby"))
    wx = [] if row.get("wxcodes") in (None, "", "M") else row["wxcodes"].split()
    tf, df, kt = _num(row.get("tmpf")), _num(row.get("dwpf")), _num(row.get("sknt"))
    return {
        "time": datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc),
        "vis_m": vis, "vis_source": src, "wx": wx,
        "fog": vis is not None and vis < FOG_VIS_M and any(t in FOG_TOKENS for t in wx),
        "low_vis": vis is not None and vis < LOW_VIS_M,
        "t": None if tf is None else round((tf - 32) / 1.8, 1),
        "td": None if df is None else round((df - 32) / 1.8, 1),
        "wind_kt": kt, "wind_dir": _num(row.get("drct")), "raw": raw}


def fetch_reports(station, start, end, fetch=http_text, sleep=time.sleep):
    url = build_url(station, start, end)
    for attempt in range(len(RETRY_WAIT_S) + 1):
        try:
            text = fetch(url)
            break
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == len(RETRY_WAIT_S):
                raise
            sleep(RETRY_WAIT_S[attempt])
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows or "valid" not in rows[0]:
        raise RuntimeError(f"unexpected IEM answer for {station}: {text[:200]!r}")
    return [parse_report(r) for r in rows]
