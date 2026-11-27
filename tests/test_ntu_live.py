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
        self._min = ntu_live.MIN_STATIONS
        ntu_live.MIN_STATIONS = 1  # most tests use one or two stations

    def tearDown(self):
        ntu_live.MIN_STATIONS = self._min

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

    def test_fallback_requested_every_run_but_not_twice_in_a_row(self):
        f = Fetcher([rec(1, 5)], {"1": fb_entry(1)})
        ntu_live.run(self.site, self.fb, self.static, fetch=f, now=NOW)
        f2 = Fetcher([rec(1, 5)], {"1": fb_entry(1)})
        st2 = ntu_live.run(self.site, self.fb, self.static, fetch=f2, now=NOW + timedelta(minutes=3))
        self.assertEqual(f2.calls, [])                     # a re-run minutes later makes no request at all
        self.assertTrue(st2["skipped"])
        f3 = Fetcher([rec(1, 5)], {"1": fb_entry(1, updated_at=1795626901)})
        ntu_live.run(self.site, self.fb, self.static, fetch=f3, now=NOW + timedelta(minutes=15))
        self.assertEqual(f3.calls, ["temperaturefallback", "data"])
        files = os.listdir(self.fb)
        self.assertEqual(len(files), 1)                    # both states in the same daily file
        with open(os.path.join(self.fb, files[0]), encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().splitlines()), 3)

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


class GuardTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.site, self.fb = os.path.join(d, "site"), os.path.join(d, "fb")
        self.static = os.path.join(d, "st.json")
        with open(self.static, "w", encoding="utf-8") as f:
            json.dump({"stations": {}}, f)
        self.many = [rec(i, 5) for i in range(1, 31)]
        self.fb_ok = {str(i): fb_entry(i) for i in range(1, 31)}

    def latest(self):
        with open(os.path.join(self.site, "obs", "ntu_latest.json"), encoding="utf-8") as f:
            return {s["id"]: s for s in json.load(f)["stations"]}

    def test_no_request_within_14_minutes(self):
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, self.fb_ok), now=NOW)
        for m in (1, 7, 13):
            f = Fetcher(self.many, self.fb_ok)
            st = ntu_live.run(self.site, self.fb, self.static, fetch=f, now=NOW + timedelta(minutes=m))
            self.assertEqual(f.calls, [])
            self.assertTrue(st["skipped"])
        f = Fetcher(self.many, self.fb_ok)
        ntu_live.run(self.site, self.fb, self.static, fetch=f, now=NOW + timedelta(minutes=14, seconds=40))
        self.assertIn("data", f.calls)

    def test_failed_request_still_counts(self):
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, self.fb_ok), now=NOW)
        with self.assertRaises(RuntimeError):
            ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher([], {}), now=NOW + timedelta(minutes=15))
        f = Fetcher(self.many, self.fb_ok)
        st = ntu_live.run(self.site, self.fb, self.static, fetch=f, now=NOW + timedelta(minutes=20))
        self.assertTrue(st["skipped"])                     # the failed attempt at +15 was a request too

    def test_too_few_stations_keeps_good_file(self):
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, self.fb_ok), now=NOW)
        path = os.path.join(self.site, "obs", "ntu_latest.json")
        before = open(path, encoding="utf-8").read()
        stale = [rec(i, 400) for i in range(1, 31)]        # all older than 6 h -> omitted
        broken = [{"station_id": i, "whatever": 1} for i in range(1, 31)]   # format change
        for k, data in enumerate((stale, broken, self.many[:5])):
            with self.assertRaises(RuntimeError):
                ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(data, self.fb_ok),
                             now=NOW + timedelta(minutes=15 * (k + 1)))
            self.assertEqual(open(path, encoding="utf-8").read(), before)

    def test_stale_or_missing_fallback_is_unknown(self):
        part = {str(i): fb_entry(i) for i in range(1, 30)}                      # station 30 missing
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, part),
                     now=NOW + timedelta(minutes=10))
        by = self.latest()
        self.assertIn("fallback_unknown", by["NTU:30"]["qc"]["flags"])
        self.assertEqual(by["NTU:1"]["qc"]["flags"], [])
        old = {str(i): fb_entry(i, fallback_active=True) for i in range(1, 31)}   # 2026-11-25T17:00:01Z
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, old),
                     now=NOW + timedelta(minutes=61))
        s = self.latest()["NTU:1"]
        self.assertIn("fallback_unknown", s["qc"]["flags"])
        self.assertNotIn("model_fallback", s["qc"]["flags"])
        self.assertFalse(s["qc"]["excluded"])

    def test_frozen_temperature_is_flagged_and_excluded(self):
        t = NOW
        for k in range(14):                                # 14 runs, 15 min apart = 3 h 15 min
            data = [rec(i, 0, time=t.strftime("%Y-%m-%d %H:%M:%S"),
                        air=(5.0 if i == 1 else 5.0 + 0.1 * k), humidity=(100.0 if i == 2 else 90.0 - k))
                    for i in range(1, 31)]
            fb = {str(i): fb_entry(i, updated_at=int(t.timestamp())) for i in range(1, 31)}
            ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(data, fb), now=t)
            t += timedelta(minutes=15)
        by = self.latest()
        self.assertIn("t_frozen", by["NTU:1"]["qc"]["flags"])
        self.assertTrue(by["NTU:1"]["qc"]["excluded"])
        self.assertNotIn("t_frozen", by["NTU:3"]["qc"]["flags"])
        self.assertNotIn("rh_constant", by["NTU:2"]["qc"]["flags"])   # only 3 h of history, needs 6 h


class PortalTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.site, self.fb = os.path.join(d, "site"), os.path.join(d, "fb")
        self.static = os.path.join(d, "st.json")
        with open(self.static, "w", encoding="utf-8") as f:
            json.dump({"stations": {}}, f)
        self.many = [rec(i, 5) for i in range(1, 31)]
        self.fb_ok = {str(i): fb_entry(i) for i in range(1, 31)}

    def load(self, rel):
        with open(os.path.join(self.site, rel), encoding="utf-8") as f:
            return json.load(f)

    def test_headers_and_manifest(self):
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, self.fb_ok), now=NOW)
        for rel, schema in (("obs/ntu_latest.json", "fognowcast.ntu_latest/1.0"),
                            ("obs/ntu_recent.json", "fognowcast.ntu_recent/1.0"),
                            ("obs/ntu_fallback_latest.json", "fognowcast.ntu_fallback/1.0"),
                            ("obs/ntu_fetch_state.json", "fognowcast.ntu_fetch_state/1.0")):
            d = self.load(rel)
            self.assertEqual(list(d)[:5], ["schema", "generated_at", "source", "run", "cadence_min"])
            self.assertEqual((d["schema"], d["run"], d["cadence_min"]), (schema, None, 15))
        m = self.load("manifest.json")
        self.assertEqual(m["schema"], "portal.manifest/1.0")
        self.assertEqual((m["id"], m["status"], m["cadence_min"], m["generated_at"]), ("fognowcast", "experimental", 15, "2026-11-25T17:20:00Z"))
        self.assertEqual(m["embed"], {"url": "index.html?embed=1", "min_height_px": 800, "gated": False})
        self.assertEqual([(c["project"], c["path"]) for c in m["consumes"]],
                         [("multiconvect", "latest_points.json"), ("nowcasting-public", "obs/metar_latest.json")])
        by = {p["id"]: p for p in m["products"]}
        self.assertEqual(set(by), {"ntu_latest", "ntu_recent", "ntu_fallback"})
        self.assertEqual(by["ntu_latest"]["data_time"], "2026-11-25T17:15:00Z")   # newest record, 5 min old
        self.assertEqual(by["ntu_recent"]["data_time"], "2026-11-25T17:15:00Z")
        self.assertEqual(by["ntu_fallback"]["data_time"], "2026-11-25T17:00:01Z")
        self.assertTrue(by["ntu_latest"]["doc"].startswith("https://github.com/ilianrvd/fogNowcast/blob/main/docs/"))
        self.assertNotIn("ntu_fetch_state", by)

    def test_manifest_keeps_fog_points_product_between_runs(self):
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, self.fb_ok), now=NOW)
        m = self.load("manifest.json")
        m["products"].append({"id": "fog_points", "generated_at": "2026-11-25T17:21:00Z"})
        m["products"].append({"id": "fog_polygons", "generated_at": "2026-11-25T17:21:00Z"})
        with open(os.path.join(self.site, "manifest.json"), "w", encoding="utf-8") as f:
            json.dump(m, f)
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, self.fb_ok), now=NOW + timedelta(minutes=15))
        by = {p["id"]: p for p in self.load("manifest.json")["products"]}
        self.assertEqual(by["fog_points"]["generated_at"], "2026-11-25T17:21:00Z")
        self.assertIn("fog_polygons", by)

    def test_manifest_not_rewritten_when_skipped_or_failed(self):
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, self.fb_ok), now=NOW)
        before = self.load("manifest.json")
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, self.fb_ok), now=NOW + timedelta(minutes=5))
        self.assertEqual(self.load("manifest.json"), before)            # throttled: nothing written
        with self.assertRaises(RuntimeError):
            ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher([], {}), now=NOW + timedelta(minutes=15))
        self.assertEqual(self.load("manifest.json"), before)            # failed: age keeps growing on the portal
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(self.many, self.fb_ok), now=NOW + timedelta(minutes=30))
        self.assertEqual(self.load("manifest.json")["generated_at"], "2026-11-25T17:50:00Z")


class LiveArchiveTests(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.site, self.fb, self.live = (os.path.join(d, x) for x in ("site", "fb", "live"))
        self.static = os.path.join(d, "st.json")
        with open(self.static, "w", encoding="utf-8") as f:
            json.dump({"stations": {}}, f)
        self._min = ntu_live.MIN_STATIONS
        ntu_live.MIN_STATIONS = 1

    def tearDown(self):
        ntu_live.MIN_STATIONS = self._min

    def rows(self, name):
        import csv, gzip
        with gzip.open(os.path.join(self.live, name), "rt", encoding="utf-8") as f:
            return list(csv.reader(f))

    def test_appends_only_new_records_and_splits_by_month(self):
        fb = {"1": fb_entry(1), "2": fb_entry(2)}
        st = ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher([rec(1, 5), rec(2, 50)], fb),
                          now=NOW, live_dir=self.live)
        self.assertEqual(st["live_archived"], 2)
        rows = self.rows("ntu_live_2026-11.csv.gz")
        self.assertEqual(rows[0], ["time", "id", "t", "rh", "td", "t_surface", "pressure_hpa", "wind_kt",
                                   "wind_dir", "flags"])
        self.assertEqual(rows[1][1], "NTU:2")                    # sorted by time
        self.assertNotIn("late", rows[1][9])                     # publication flag is not archived
        self.assertEqual(rows[2][7], "10.0")                     # kt
        # 15 min later: station 1 has a new record, station 2 repeats the old one
        later = NOW + timedelta(minutes=15)
        data = [rec(1, -10), rec(2, 50)]
        st = ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(data, fb), now=later, live_dir=self.live)
        self.assertEqual(st["live_archived"], 1)
        self.assertEqual(len(self.rows("ntu_live_2026-11.csv.gz")), 4)
        # a record from the next month goes to its own file
        nxt = datetime(2026, 12, 1, 0, 5, tzinfo=UTC)
        data = [dict(rec(1, 0), time="2026-12-01 00:00:00")]
        ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher(data, fb), now=nxt, live_dir=self.live)
        self.assertEqual(len(self.rows("ntu_live_2026-12.csv.gz")), 2)

    def test_archive_failure_does_not_stop_publishing(self):
        blocker = os.path.join(os.path.dirname(self.live), "blocker")
        open(blocker, "w").close()                               # a file where a directory is expected
        st = ntu_live.run(self.site, self.fb, self.static, fetch=Fetcher([rec(1, 5)], {"1": fb_entry(1)}),
                          now=NOW, live_dir=os.path.join(blocker, "live"))
        self.assertIsNone(st["live_archived"])
        self.assertTrue(os.path.exists(os.path.join(self.site, "obs", "ntu_latest.json")))


if __name__ == "__main__":
    unittest.main()
