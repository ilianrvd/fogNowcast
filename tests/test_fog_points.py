import unittest
from datetime import datetime, timezone

from core import fog_points as fp

NOW = datetime(2026, 11, 26, 5, 20, tzinfo=timezone.utc)


def series(t=2.0, td=0.0, ws=6):
    return [{"valid": f"2026-11-26T{h:02d}:00:00Z", "lead_h": h, "t": t, "td": td, "wind_kt": ws,
             "wind_dir": 240, "cloud_low_pct": 20} for h in range(0, 24)]


MODEL = {"run": "2026-11-26T00:00:00Z", "points": [{"id": "NTU:1", "model_elev_m": 650.0, "series": series()},
                                                   {"id": "LBSF", "model_elev_m": 548.0, "series": series()}]}
POINTS = {"points": [{"id": "NTU:1", "name": "x", "lat": 42.4, "lon": 23.8, "elev_m": 700},
                     {"id": "LBSF", "name": "y", "lat": 42.7, "lon": 23.4, "elev_m": 531}]}
NTU = {"obs_time_latest": "2026-11-26T05:15:00Z", "stations": [
    {"id": "NTU:1", "time": "2026-11-26T05:15:00Z", "t": 0.0, "td": -1.0, "wind_kt": 2.0, "qc": {"excluded": False}}]}


class FogPoints(unittest.TestCase):
    def test_residual_decays(self):
        doc = fp.build(MODEL, NTU, None, POINTS, NOW)
        p = doc["points"][0]
        self.assertEqual(p["obs_source"], "ntu")
        self.assertEqual(p["residual"]["t"], -2.0)            # 0.0 - 2.0
        first, last = p["series"][0], p["series"][-1]
        self.assertLess(first["t"], last["t"])                 # correction fades towards the model
        self.assertAlmostEqual(first["t"], 0.0, delta=0.1)      # starts at the observation
        self.assertAlmostEqual(last["t"], 2.0 - 2.0 * 0.53, delta=0.1)   # exp(-5.75/9) of the residual left
        self.assertGreater(first["corr_weight"], last["corr_weight"])
        self.assertEqual([r["lead_h"] for r in p["series"]], list(range(7)))

    def test_no_observation_is_model_only(self):
        p = fp.build(MODEL, NTU, None, POINTS, NOW)["points"][1]    # LBSF without METAR
        self.assertIsNone(p["obs_source"])
        self.assertIsNone(p["residual"])
        self.assertEqual(p["series"][0]["t"], 2.0)
        self.assertEqual(p["series"][0]["corr_weight"], 0)
        self.assertEqual(p["series"][0]["deficit"], 2.0)

    def test_stale_or_excluded_observation_ignored(self):
        ntu = {"stations": [dict(NTU["stations"][0], time="2026-11-26T03:00:00Z")]}
        self.assertIsNone(fp.build(MODEL, ntu, None, POINTS, NOW)["points"][0]["obs_source"])
        ntu = {"stations": [dict(NTU["stations"][0], qc={"excluded": True})]}
        self.assertIsNone(fp.build(MODEL, ntu, None, POINTS, NOW)["points"][0]["obs_source"])

    def test_metar_used_for_airport(self):
        metar = {"fetched_at": "2026-11-26T05:10:00Z", "reports": [
            {"id": "LBSF", "time": "2026-11-26T05:00:00Z", "t": 0, "td": 0, "wind_kt": 0}]}
        p = fp.build(MODEL, NTU, metar, POINTS, NOW)["points"][1]
        self.assertEqual(p["obs_source"], "metar")
        self.assertEqual(p["series"][0]["wind_kt"], 0)         # wind never negative
        self.assertFalse(fp.build(MODEL, NTU, metar, POINTS, NOW)["calibrated"])

    def test_deficit_below_zero_uses_frost_point(self):
        ntu = {"stations": [dict(NTU["stations"][0], t=-10.0, td=-11.0)]}
        m = {"run": MODEL["run"], "points": [{"id": "NTU:1", "series": series(t=-10.0, td=-11.0)}]}
        d = fp.build(m, ntu, None, POINTS, NOW)["points"][0]["series"][0]["deficit"]
        self.assertLess(d, 1.0)                                # over ice the deficit is below t - td

class Cli(unittest.TestCase):
    def test_stations_file_is_merged(self):
        import json, os, tempfile
        d = tempfile.mkdtemp()
        w = lambda n, o: json.dump(o, open(os.path.join(d, n), "w"))
        w("m.json", MODEL); w("n.json", NTU)
        w("p.json", {"points": [POINTS["points"][1]]}); w("s.json", {"stations": [POINTS["points"][0]]})
        fp.main(["--model", d + "/m.json", "--ntu", d + "/n.json", "--points", d + "/p.json",
                 "--stations", d + "/s.json", "--out", d + "/o.json"])
        ids = [p["id"] for p in json.load(open(d + "/o.json"))["points"]]
        self.assertEqual(sorted(ids), ["LBSF", "NTU:1"])
        self.assertFalse(os.path.exists(d + "/o.json.tmp"))


if __name__ == "__main__":
    unittest.main()
