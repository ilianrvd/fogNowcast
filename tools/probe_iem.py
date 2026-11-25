"""One-off probe of the IEM ASOS archive for the five airports (read-only, writes nothing).

Run only from GitHub Actions (see .github/workflows/probe-iem.yml); the sandbox cannot reach IEM.
Prints, per airport and month, the number of METAR/SPECI reports, how many carry FG, how many have
visibility under 1000 m, plus the first/last report time and a few raw reports, so we can see
whether the data are complete before writing the calibration.
"""
import collections
import csv
import io
import sys
import time
import urllib.parse
import urllib.request

URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
UA = "fogNowcast-probe/0.1 (+https://github.com/ilianrvd/fogNowcast)"
STATIONS = ["LBSF", "LBBG", "LBWN", "LBPD", "LBGO"]
START, END = "2026-01-27", "2026-11-25"  # IEM end date is exclusive
SM_TO_M = 1609.344


def fetch(station):
    q = [("station", station), ("data", "all"), ("tz", "Etc/UTC"), ("format", "onlycomma"),
         ("latlon", "no"), ("elev", "no"), ("missing", "M"), ("trace", "T"), ("direct", "no"),
         ("report_type", "3"), ("report_type", "4"),
         ("year1", START[:4]), ("month1", START[5:7].lstrip("0")), ("day1", START[8:].lstrip("0")),
         ("year2", END[:4]), ("month2", END[5:7].lstrip("0")), ("day2", END[8:].lstrip("0"))]
    req = urllib.request.Request(URL + "?" + urllib.parse.urlencode(q), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read().decode("utf-8", "replace")


def main() -> int:
    for st in STATIONS:
        text = fetch(st)
        rows = list(csv.DictReader(io.StringIO(text)))
        print(f"== {st}: {len(rows)} reports; columns: {list(rows[0].keys()) if rows else '-'}")
        if not rows:
            print(text[:300])
            continue
        print(f"   first {rows[0]['valid']}  last {rows[-1]['valid']}")
        per = collections.OrderedDict()
        for r in rows:
            m = per.setdefault(r["valid"][:7], collections.Counter())
            m["reports"] += 1
            wx = (r.get("wxcodes") or "M")
            m["FG"] += "FG" in wx and wx != "M"
            m["BR"] += "BR" in wx
            try:
                m["vis<1000m"] += float(r["vsby"]) * SM_TO_M < 1000
            except ValueError:
                m["vis_missing"] += 1
            m["no_tmpf"] += r["tmpf"] == "M"
        for mon, c in per.items():
            print(f"   {mon}: " + " ".join(f"{k}={v}" for k, v in c.items()))
        for r in [x for x in rows if "FG" in (x.get("wxcodes") or "")][:3]:
            print(f"   sample: {r['valid']} vsby={r['vsby']}sm wx={r['wxcodes']} | {r['metar']}")
        time.sleep(2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
