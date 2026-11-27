import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

try:
    import numpy as np
except ImportError:  # the reader runs in Actions with numpy
    np = None

from core import fog_fields as ff


def write(d, ny=3, nx=4, nt=2, schema="multiconvect.fog_fields/1.0", lat_desc=False):
    lat = np.array([42.0, 42.0625, 42.125][:ny], "float32")
    if lat_desc:
        lat = lat[::-1]
    lon = np.array([23.0 + 0.0625 * k for k in range(nx)], "float32")
    t0 = int(datetime(2026, 11, 26, 18, tzinfo=timezone.utc).timestamp())
    vt = np.array([t0 + 3600 * k for k in range(nt)], "int64")
    shape = (nt, ny, nx)
    t2m = np.full(shape, 500, "int16")                      # 5.00 C
    t2m[1] = 600
    t2m[0, 0, 0] = -32768                                    # missing
    td2m = np.full(shape, 300, "int16")
    u10 = np.full(shape, 300, "int16")                       # 3 kt
    v10 = np.full(shape, 400, "int16")                       # 4 kt -> 5 kt
    clcl = np.full(shape, 500, "int16")                      # 50 %
    hs = np.full((ny, nx), 550, "int16")
    np.savez_compressed(os.path.join(d, "f.npz"), lat=lat, lon=lon, valid_time=vt, hsurf=hs, t2m=t2m, td2m=td2m,
                        u10=u10, v10=v10, clcl=clcl)
    sc = lambda s: {"scale": s, "offset": 0.0, "fill": -32768, "dtype": "int16"}
    meta = {"schema": schema, "generated_at": "2026-11-26T22:59:00Z", "source": "MultiConvect",
            "run": "2026-11-26T18:00:00Z", "cadence_min": 360,
            "grid": {"ny": ny, "nx": nx}, "vars": {"t2m": sc(0.01), "td2m": sc(0.01), "u10": sc(0.01),
                                                    "v10": sc(0.01), "clcl": sc(0.1), "hsurf": sc(1.0)},
            "future_key": 1}
    json.dump(meta, open(os.path.join(d, "m.json"), "w"))
    return os.path.join(d, "f.npz"), os.path.join(d, "m.json")


@unittest.skipIf(np is None, "numpy not installed")
class FogFields(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_decode_and_shapes(self):
        f = ff.load(*write(self.d))
        self.assertEqual(f["t2m"].shape, (2, 3, 4))
        self.assertTrue(np.isnan(f["t2m"][0, 0, 0]))
        self.assertAlmostEqual(f["t2m"][1, 2, 3], 6.0)
        self.assertAlmostEqual(f["clcl"][0, 1, 1], 50.0)
        self.assertEqual(f["hsurf"][0, 0], 550.0)
        self.assertEqual(f["times"][1], datetime(2026, 11, 26, 19, tzinfo=timezone.utc))
        self.assertAlmostEqual(ff.wind_kt(f["u10"][0, 1, 1], f["v10"][0, 1, 1]), 5.0)

    def test_format_errors(self):
        with self.assertRaises(ff.FormatError):
            ff.load(*write(self.d, schema="multiconvect.fog_fields/2.0"))
        with self.assertRaises(ff.FormatError):
            ff.load(*write(tempfile.mkdtemp(), lat_desc=True))
        npz, meta = write(tempfile.mkdtemp())
        m = json.load(open(meta)); m["grid"]["ny"] = 9; json.dump(m, open(meta, "w"))
        with self.assertRaises(ff.FormatError):
            ff.load(npz, meta)

    def test_bilinear(self):
        f = ff.load(*write(self.d))
        lat, lon = f["lat"], f["lon"]
        self.assertAlmostEqual(ff.bilinear(f["t2m"][1], lat, lon, 42.03, 23.1), 6.0)
        self.assertTrue(ff.bilinear(f["t2m"][0], lat, lon, 42.01, 23.01) != ff.bilinear(f["t2m"][0], lat, lon, 42.01, 23.01))  # NaN
        self.assertTrue(np.isnan(ff.bilinear(f["t2m"][1], lat, lon, 50.0, 23.1)))

    def test_check_against_points(self):
        f = ff.load(*write(self.d))
        pts = {"run": "2026-11-26T18:00:00Z", "points": [{"id": "X", "lat": 42.1, "lon": 23.1, "series": [
            {"valid": "2026-11-26T19:00:00Z", "t": 6.5, "td": 3.0, "wind_kt": 5.0}]}]}
        r = ff.check(f, pts)
        self.assertTrue(r["same_run"])
        self.assertEqual((r["ny"], r["nx"], r["hours"]), (3, 4, 2))
        self.assertAlmostEqual(r["grid_vs_points_mae"]["t"], 0.5)
        self.assertAlmostEqual(r["grid_vs_points_mae"]["wind_kt"], 0.0)


if __name__ == "__main__":
    unittest.main()
