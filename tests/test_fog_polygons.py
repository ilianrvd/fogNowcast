import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

try:
    import numpy as np
    import contourpy  # noqa: F401
except ImportError:
    np = None

from verification import core

NOW = datetime(2026, 11, 27, 3, 20, tzinfo=timezone.utc)


def grid(nt=8, ny=21, nx=31, t=5.0, td=2.0, blob=True):
    """Flat 500 m grid 42.0-43.25 N, 23.0-24.875 E; a saturated blob in the middle, calm, clear."""
    lat = 42.0 + 0.0625 * np.arange(ny)
    lon = 23.0 + 0.0625 * np.arange(nx)
    t0 = datetime(2026, 11, 27, 0, tzinfo=timezone.utc)
    times = [t0 + timedelta(hours=k) for k in range(nt)]
    T = np.full((nt, ny, nx), t)
    TD = np.full((nt, ny, nx), td)
    if blob:
        TD[:, 8:13, 12:19] = t - 0.2          # deficit 0.2 C
        TD[:, 7, 12:19] = t - 0.8             # a 0.5-1.0 C rim on one side
    return {"run": "2026-11-27T00:00:00Z", "lat": lat, "lon": lon, "times": times,
            "hsurf": np.full((ny, nx), 500.0), "t2m": T, "td2m": TD,
            "u10": np.full((nt, ny, nx), 1.0), "v10": np.full((nt, ny, nx), 1.0), "clcl": np.zeros((nt, ny, nx))}


def ntu(stations):
    return {"stations": [dict({"name": s["id"], "qc": {"excluded": False}, "time": "2026-11-27T03:15:00Z"}, **s)
                         for s in stations]}


@unittest.skipIf(np is None, "numpy/contourpy not installed")
class FogPolygons(unittest.TestCase):
    def setUp(self):
        from core import fog_polygons as fpg
        self.m = fpg

    def test_sat_deficit_matches_scalar(self):
        for t, td in [(5.0, 2.0), (-10.0, -11.0), (-3.0, -3.0), (2.0, 4.0)]:
            self.assertAlmostEqual(float(self.m.sat_deficit(np.array(t), np.array(td))), core.sat_deficit(t, td), 6)

    def test_residual_uses_lapse_rate(self):
        f = grid(blob=False)
        # station 300 m above the model surface, exactly as cold as the lapse rate says: residual 0
        obs = self.m.observations(ntu([{"id": "NTU:1", "lat": 42.5, "lon": 23.5, "elev_m": 800,
                                        "t": 5.0 - 0.0065 * 300, "td": 2.0 - 0.0018 * 300, "wind_kt": 1.41}]),
                                  None, {}, NOW)
        r = self.m.residuals(f, obs)[0]["res"]
        self.assertAlmostEqual(r["t"], 0.0, 2)
        self.assertAlmostEqual(r["td"], 0.0, 2)

    def test_spread_by_distance_and_height(self):
        f = grid(blob=False)
        st = [{"id": "A", "lat": 42.5, "lon": 23.5, "elev": 500.0, "time": NOW, "res": {"t": -2.0}}]
        r = self.m.spread(f, st, "t", NOW)
        i, j = 8, 8                                           # the station's cell
        self.assertLess(r[i, j], -1.5)
        self.assertAlmostEqual(r[0, -1], 0.0, 3)              # ~ 100 km away: no correction
        high = [dict(st[0], elev=1500.0)]                     # station 1000 m above the valley grid
        self.assertAlmostEqual(self.m.spread(f, high, "t", NOW)[i, j], 0.0, 3)

    def test_polygons_classes_and_dynamic_mask(self):
        f = grid()
        cf = self.m.corrected_fields(f, [], NOW, NOW.replace(minute=0))
        self.assertEqual(sorted(cf), list(range(0, 5)))       # grid has 8 hours from 00: 03..07 -> leads 0..4
        c = cf[0]
        feats = self.m.polygons(f["lat"], f["lon"], c["deficit"], c["dynamic"])
        self.assertEqual([x["properties"]["class"] for x in feats], ["fog_conditions", "near_saturation"])
        ring = feats[0]["geometry"]["coordinates"][0][0]
        lons, lats = [p[0] for p in ring], [p[1] for p in ring]
        self.assertTrue(23.6 < min(lons) and max(lons) < 24.3 and 42.4 < min(lats) and max(lats) < 42.9)
        self.assertEqual(self.m.polygons(f["lat"], f["lon"], c["deficit"], np.ones_like(c["dynamic"])), [])

    def test_build_header_check_and_manifest(self):
        f = grid()
        stations = [{"id": f"NTU:{k}", "lat": 42.2 + 0.1 * k, "lon": 23.4 + 0.1 * k, "elev_m": 500,
                     "t": 4.0, "td": 1.0, "wind_kt": 1.0} for k in range(6)]
        metar = {"reports": [{"id": "LBSF", "time": "2026-11-27T03:00:00Z", "t": 4.0, "td": 3.9, "wind_kt": 0}]}
        airports = {"LBSF": {"lat": 42.7, "lon": 23.4, "elev_m": 531}}
        doc = self.m.build(f, ntu(stations), metar, airports, NOW)
        self.assertEqual(list(doc)[1:6], ["schema", "generated_at", "source", "run", "cadence_min"])
        self.assertEqual(doc["schema"], "fognowcast.fog_polygons/1.0")
        self.assertEqual(doc["inputs"]["stations_used"], 7)
        self.assertEqual(doc["check"]["leave_one_out_t0"]["t"]["n"], 7)
        self.assertTrue(all("lead_h" in x["properties"] and "valid" in x["properties"] for x in doc["features"]))
        d = tempfile.mkdtemp()
        path = os.path.join(d, "manifest.json")
        json.dump({"products": [{"id": "fog_points"}]}, open(path, "w"))
        self.assertTrue(self.m.update_manifest(path, doc))
        self.assertEqual([p["id"] for p in json.load(open(path))["products"]], ["fog_points", "fog_polygons"])

    def test_stale_and_excluded_stations_are_dropped(self):
        st = ntu([{"id": "NTU:1", "lat": 42.5, "lon": 23.5, "elev_m": 500, "t": 4.0, "td": 1.0, "wind_kt": 1.0}])
        st["stations"][0]["time"] = "2026-11-27T01:00:00Z"
        self.assertEqual(self.m.observations(st, None, {}, NOW), [])
        st["stations"][0].update(time="2026-11-27T03:15:00Z", qc={"excluded": True})
        self.assertEqual(self.m.observations(st, None, {}, NOW), [])


if __name__ == "__main__":
    unittest.main()
