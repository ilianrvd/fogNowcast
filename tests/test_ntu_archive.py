import gzip
import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone

from ingest.ntu_archive import import_archive, snapshot_candidates
from qc.ntu_qc import classify_lag, dew_point, kmh_to_kt, normalize_record


def rec(sid, t, **kw):
    base = {"station_id": sid, "scp": 1000 + sid, "name": f"S{sid}", "nameENG": f"S{sid}", "lat": 42.1,
            "lon": 23.1, "air": 1.5, "surface": 2.0, "humidity": 80.0, "pressure": 900.0,
            "wind": 18.52, "wind_dir": 100.0, "time": t}
    base.update(kw)
    return base


def inner_zip(day, slots):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        for hhmm, payload in slots:
            body = payload if isinstance(payload, bytes) else ("﻿" + json.dumps(payload)).encode("utf-8")
            z.writestr(f"stationsData_{day}_{hhmm}.json", body)
    return b.getvalue()


def outer_zip(files):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_STORED) as z:
        z.writestr("2026/", b"")
        for name, data in files.items():
            z.writestr(name, data)
    return b.getvalue()


class QcTests(unittest.TestCase):
    def test_wind_units(self):
        self.assertAlmostEqual(kmh_to_kt(18.52), 10.0)

    def test_dew_point(self):
        self.assertAlmostEqual(dew_point(10.0, 100.0), 10.0, places=6)
        self.assertAlmostEqual(dew_point(20.0, 50.0), 9.26, places=1)

    def test_zero_means_missing(self):
        n = normalize_record(rec(1, "2026-01-27 08:15:00", surface=0, humidity=0, pressure=0))
        self.assertIsNone(n["t_surface"])
        self.assertIsNone(n["rh"])
        self.assertIsNone(n["td"])
        self.assertIsNone(n["pressure_hpa"])
        self.assertEqual(sorted(n["flags"]), ["pressure_missing", "rh_missing", "surface_missing"])

    def test_wind_zero_both(self):
        n = normalize_record(rec(1, "2026-01-27 08:15:00", wind=0, wind_dir=0))
        self.assertEqual(n["wind_kt"], 0)
        self.assertIsNone(n["wind_dir"])
        self.assertIn("wind_zero_both", n["flags"])

    def test_surface_suspect_and_rh_over_100(self):
        n = normalize_record(rec(1, "2026-01-27 08:15:00", surface=77.0, humidity=101.0))
        self.assertIn("surface_suspect", n["flags"])
        self.assertIn("rh_over_100", n["flags"])
        self.assertEqual(n["rh"], 100.0)

    def test_lag_classes(self):
        self.assertEqual(classify_lag(3), "ok")
        self.assertEqual(classify_lag(-5), "ok")
        self.assertEqual(classify_lag(45), "stale")
        self.assertEqual(classify_lag(-60), "future")

    def test_dst_ambiguous_hour(self):
        c = snapshot_candidates("x/stationsData_20261025_0330.json")
        self.assertEqual(len(c), 2)
        self.assertEqual((c[1] - c[0]).total_seconds(), 3600)
        self.assertEqual(len(snapshot_candidates("x/stationsData_20261124_0000.json")), 1)


class ImportTests(unittest.TestCase):
    def run_import(self, files):
        d = tempfile.mkdtemp()
        zp = os.path.join(d, "o.zip")
        with open(zp, "wb") as f:
            f.write(outer_zip(files))
        out = os.path.join(d, "out")
        return out, import_archive(zp, out)

    def test_import(self):
        # local 10:30 (EET, UTC+2) on 2026-01-27 == 08:30Z
        day1 = inner_zip("20260127", [
            ("1030", [rec(1, "2026-01-27 08:30:00"),                     # fresh
                      rec(2, "2023-09-03 19:45:00"),                     # ghost: stale
                      rec(3, "2026-01-27 08:30:00", humidity=0)]),       # rh missing
            ("1045", [rec(1, "2026-01-27 08:30:00"),                     # duplicate (lag 15)
                      rec(1, "2026-01-27 08:45:00"),
                      rec(3, "2026-01-27 08:45:00", humidity=0)]),
            ("1100", b"{broken"),                                         # corrupt
        ])
        out, res = self.run_import({"2026/01/meteoData_2026-01-27.zip": day1})
        meta = res["meta"]
        self.assertEqual(meta["counts"]["dropped_stale"], 1)
        self.assertEqual(meta["counts"]["dropped_duplicate"], 1)
        self.assertEqual(len(meta["bad_files"]), 1)
        self.assertEqual(meta["rows_written"], {"2026-01": 4})
        self.assertEqual(meta["ghost_stations_no_fresh_rows"], ["NTU:2"])
        with gzip.open(os.path.join(out, "ntu_2026-01.csv.gz"), "rt", encoding="utf-8") as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], "time,id,t,rh,td,t_surface,pressure_hpa,wind_kt,wind_dir,flags")
        self.assertEqual(lines[1].split(",")[:2], ["2026-01-27T08:30:00Z", "NTU:1"])
        self.assertIn("10.0", lines[1].split(","))            # 18.52 km/h -> 10.0 kt
        st = res["stations"]
        self.assertTrue(st["NTU:1"]["active"])
        self.assertFalse(st["NTU:3"]["rh_usable"])
        self.assertNotIn("NTU:2", st)
        self.assertEqual(st["NTU:1"]["pressure_mean_hpa"], 900.0)

    def test_time_comes_from_record_not_file_name(self):
        # file name 00:00 local on 2026-11-24 == 22:00Z on 11-23; record time is that UTC time
        day = inner_zip("20261124", [("0000", [rec(1, "2026-11-23 22:00:00")])])
        out, res = self.run_import({"2026/11/meteoData_2026-11-24.zip": day})
        self.assertEqual(res["meta"]["rows_written"], {"2026-11": 1})
        with gzip.open(os.path.join(out, "ntu_2026-11.csv.gz"), "rt", encoding="utf-8") as f:
            self.assertIn("2026-11-23T22:00:00Z", f.read())


class ElevationTests(unittest.TestCase):
    def test_isa_elevation(self):
        from tools.fetch_ntu_elevations import isa_elevation
        self.assertAlmostEqual(isa_elevation(1016.0), 0.0, places=3)
        self.assertAlmostEqual(isa_elevation(900.0), 1000, delta=60)   # ~ 1.0 km


if __name__ == "__main__":
    unittest.main()
