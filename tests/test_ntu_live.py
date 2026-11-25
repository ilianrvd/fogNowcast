import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from ingest import ntu_live

UTC = timezone.utc
NOW = datetime(2026, 11, 25, 17, 20, tzinfo=UTC)


def rec(sid, minutes_ago, **kw):
    t = NOW - timedelta(minutes=minutes_ago)
    base = {"station_id": sid, "scp": 1000 + sid, "name": f"S{sid}", "nameENG": f"S{sid}", "lat": 42.0,
            "lon": 23.0, "air": 1.0, "surface": 2.0, "humidity": 90.0, "pressure": 930.0, "wind": 18.52,
            "wind_dir": 90.0, "time": t.strftime("%Y-%m-%d %H:%M:%S")}
    base.update(kw)
    return base


def fb_entry(sid, **kw):
    base = {"scp": 1000 + sid, "use_model": False, "source": "station", "fallback_reason": None,
            "fallback_active": False, "fallback_since_bg": None, "status": "ok", "difference": 1.0,
            "abs_difference": 1.0, "station_temp": 2.0, "model_temp": 1.0, "hits5": 0, "seriousHits": 0,
            "consecutive5": 0, "consecutive10": 0, "consecutiveOk": 0, "updated_at": 1795626001}
    base.update(kw)
    return base


class Fetcher:
    def __init__(self, data, fallback):
        self.data, self.fallback, self.calls = data, fallback, []

    def __call__(self, endpoint):
        self.calls.append(endpoint)
        return self.data if endpoint == "data" else self.fallback


class LiveTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.site, self.fb = os.path.join(d, "site"), os.path.join(d, "fb")
        self.static = os.path.join(d, "st.json")
        with open(self.static, "w", encoding="utf-8") as f:
            json.dump({"stations": {"NTU:1": {"elev_m": 700}}}, f)

    def test_latest_flags_and_dedupe(self):
        data = [rec(1, 5), rec(1, 20),                   # duplicate station: newest kept
                rec(2, 45),                              # late
                rec(3, 900),                             # omitted (ghost)
                rec(4, 5, surface=0)]                    # surface missing
        fb = {"1": fb_entry(1), "2": fb_entry(2, fallback_active=True, status="fallback"), "4": fb_entry(4)}
        f = Fetcher(data, fb)
        st = ntu_live.run(self.site, self.fb, self.static, fetch=f, now=NOW)
        self.assertEqual(f.calls, ["temperaturefallback", "data"])
        self.assertEqual(st["stations"], 3)
        self.assertEqual(st["omitted"], 1)
        latest = json.load(open(os.path.join(self.site, "obs", "ntu_latest.json"), encoding="utf-8"))
        by = {s["id"]: s for s in latest["stations"]}
        self.assertEqual(by["NTU:1"]["age_min"], 5)
        self.assertEqual(by["NTU:1"]["elev_m"], 700)
        self.assertEqual(by["NTU:1"]["wind_kt"], 10.0)
        self.assertIn("late", by["NTU:2"]["qc"]["flags"])
        self.assertTrue(by["NTU:2"]["qc"]["excluded"])
        self.assertIn("model_fallback", by["NTU:2"]["qc"]["flags"])
        self.assertIn("surface_missing", by["NTU:4"]["qc"]["flags"])
        self.assertNotIn("NTU:3", by)

    def test_fallback_requested_about_hourly(self):
        f = Fetcher([rec(1, 5)], {"1": fb_entry(1)})
        ntu_live.run(self.site, self.fb, self.static, fetch=f, now=NOW)
        f2 = Fetcher([rec(1, 5)], {"1": fb_entry(1)})
        ntu_live.run(self.site, self.fb, self.static, fetch=f2, now=NOW + timedelta(minutes=15))
        self.assertEqual(f2.calls, ["data"])               # fallback copy still fresh
        f3 = Fetcher([rec(1, 5)], {"1": fb_entry(1, updated_at=1795629601)})
        ntu_live.run(self.site, self.fb, self.static, fetch=f3, now=NOW + timedelta(minutes=60))
        self.assertEqual(f3.calls, ["temperaturefallback", "data"])

    def test_fallback_archive_is_idempotent(self):
        fallback = ntu_live.compact_fallback({"1": fb_entry(1), "2": fb_entry(2)}, NOW)
        self.assertTrue(ntu_live.archive_fallback(self.fb, fallback))
        self.assertFalse(ntu_live.archive_fallback(self.fb, fallback))
        files = os.listdir(self.fb)
        self.assertEqual(files, ["fallback_2026-11-25.csv"])
        lines = open(os.path.join(self.fb, files[0]), encoding="utf-8").read().splitlines()
        self.assertEqual(len(lines), 3)                    # header + 2 stations
        self.assertTrue(lines[1].startswith("2026-11-25T17:00:01Z,NTU:1,1001,ok,station,0,0,"))

    def test_recent_window_and_dedupe(self):
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher([rec(1, 5)], {"1": fb_entry(1)}), now=NOW)
        later = NOW + timedelta(hours=25)
        data = [rec(1, 5)]
        for d in data:
            d["time"] = (later - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(data, {"1": fb_entry(1)}), now=later)
        recent = json.load(open(os.path.join(self.site, "obs", "ntu_recent.json"), encoding="utf-8"))
        self.assertEqual(len(recent["rows"]), 1)           # the first row is older than 24 h

    def test_failure_leaves_files(self):
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher([rec(1, 5)], {"1": fb_entry(1)}), now=NOW)
        path = os.path.join(self.site, "obs", "ntu_latest.json")
        before = open(path, encoding="utf-8").read()
        with self.assertRaises(RuntimeError):
            ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher([], {}), now=NOW + timedelta(minutes=15))
        self.assertEqual(open(path, encoding="utf-8").read(), before)


if __name__ == "__main__":
    unittest.main()
