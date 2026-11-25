import gzip
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

from correction import fit
from correction.hourly import hourly_obs, row_to_obs, sun_elevation_deg
from correction.openmeteo import fetch_station

UTC = timezone.utc
HEADER = "time,id,t,rh,td,t_surface,pressure_hpa,wind_kt,wind_dir,flags\n"


def write_csv(path, rows):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(HEADER + "".join(r + "\n" for r in rows))


class HourlyTests(unittest.TestCase):
    def test_nearest_record_within_15_min_ties_earlier(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ntu_2026-03.csv.gz"
            write_csv(p, [
                "2026-03-01T00:15:00Z,NTU:1,1.0,90,0.0,,950,5,90,",
                "2026-03-01T23:45:00Z,NTU:1,9.0,90,8.0,,950,5,90,",   # belongs to 2026-03-02T00:00, offset 15
                "2026-03-02T00:00:00Z,NTU:1,2.0,90,1.0,,950,5,90,",   # exact, wins
                "2026-03-01T05:30:00Z,NTU:1,3.0,90,2.0,,950,5,90,",   # 30 min away, dropped
                "2026-03-01T01:00:00Z,NTU:2,4.0,90,3.0,,950,5,90,",   # station not wanted
            ])
            obs = hourly_obs([p], ["NTU:1"])
            self.assertEqual(obs[("NTU:1", datetime(2026, 3, 2, 0, tzinfo=UTC))]["t"], 2.0)
            self.assertEqual(obs[("NTU:1", datetime(2026, 3, 1, 0, tzinfo=UTC))]["t"], 1.0)
            self.assertNotIn(("NTU:1", datetime(2026, 3, 1, 5, tzinfo=UTC)), obs)
            self.assertFalse(any(k[0] == "NTU:2" for k in obs))

    def test_unusable_values_are_none_not_zero(self):
        o = row_to_obs({"t": "", "td": "", "rh": "", "wind_kt": "0.0", "wind_dir": "", "flags": "t_missing|wind_zero_both"})
        self.assertIsNone(o["t"])
        self.assertIsNone(o["ws"])
        self.assertIsNone(o["dd"])

    def test_sun_elevation(self):
        noon = sun_elevation_deg(datetime(2026, 6, 21, 10, 0, tzinfo=UTC), 42.7, 23.4)
        night = sun_elevation_deg(datetime(2026, 6, 21, 22, 0, tzinfo=UTC), 42.7, 23.4)
        self.assertGreater(noon, 60)
        self.assertLess(night, 0)


class FitTests(unittest.TestCase):
    def pairs(self, n, bias_t, noise_start=0, sid="NTU:1"):
        t0 = datetime(2026, 1, 28, tzinfo=UTC)
        out = []
        for i in range(n):
            h = t0 + timedelta(hours=i)
            t_obs = 5.0 + (i % 7) * 0.1
            obs = {"t": t_obs, "td": t_obs - 1, "dd": 1.0, "rh": 97.0, "ws": 2.0, "wd": 90.0}
            mod = {"t": t_obs + bias_t, "td": t_obs - 1 + bias_t, "dd": 1.0, "ws": 2.0, "wd": 100.0}
            out.append({"station": sid, "time": h, "obs": obs, "mod": mod,
                        "regime": fit.regime(obs, h, 42.7, 23.4)})
        return out

    def test_cells_need_min_n_and_fall_back(self):
        train = self.pairs(40, bias_t=2.0)
        cells = fit.fit_cells(train)
        hit = fit.lookup(cells, "NTU:1", "t", ("night", "calm", "sat"))
        self.assertIsNotNone(hit)
        self.assertAlmostEqual(hit[1], 2.0)
        # a regime without a full cell falls back to the saturation or station level
        hit2 = fit.lookup(cells, "NTU:1", "t", ("day", "windy", "dry"))
        self.assertIsNotNone(hit2)
        self.assertEqual(hit2[0], "station")
        self.assertIsNone(fit.lookup(fit.fit_cells(self.pairs(10, 2.0)), "NTU:1", "t", ("night", "calm", "sat")))

    def test_correction_helps_on_test_and_is_accepted(self):
        cells = fit.fit_cells(self.pairs(60, bias_t=2.0))
        ev = fit.evaluate(self.pairs(40, bias_t=2.0), cells)
        r = ev["NTU:1"]["t"]
        self.assertTrue(r["accepted"])
        self.assertLess(r["mae_after"], 0.01)
        self.assertGreater(r["mae_before"], 1.9)
        self.assertEqual(r["coverage"], 1.0)

    def test_correction_rejected_when_bias_changes(self):
        cells = fit.fit_cells(self.pairs(60, bias_t=2.0))
        ev = fit.evaluate(self.pairs(40, bias_t=-2.0), cells)  # bias flips sign: correction makes it worse
        self.assertFalse(ev["NTU:1"]["t"]["accepted"])

    def test_wind_direction_uses_smallest_angle_and_needs_wind(self):
        self.assertAlmostEqual(fit.ang_diff(350, 10), -20)
        self.assertAlmostEqual(fit.ang_diff(10, 350), 20)
        self.assertIsNone(fit.error("wd", {"wd": 10}, {"wd": 350, "ws": 1.0}))
        self.assertAlmostEqual(fit.corrected("wd", 5, 20), 345)
        self.assertEqual(fit.corrected("ws", 1.0, 3.0), 0.0)


class OpenMeteoTests(unittest.TestCase):
    DATA = {"hourly": {"time": ["2026-01-27T00:00", "2026-01-27T01:00"], "temperature_2m": [1.0, None],
                       "dew_point_2m": [0.0, 0.5], "wind_speed_10m": [3.0, 4.0], "wind_direction_10m": [90, 100]}}

    def test_retries_then_succeeds(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if len(calls) < 3:
                raise urllib.error.URLError("timeout")
            return self.DATA
        out = fetch_station(42.7, 23.4, 531, "2026-01-27", "2026-01-27", fetch=fetch, sleep=lambda s: None)
        self.assertEqual(len(calls), 3)
        self.assertIn("wind_speed_unit=kn", calls[0])
        h0 = datetime(2026, 1, 27, 0, tzinfo=UTC)
        self.assertEqual(out[h0]["dd"], 1.0)
        self.assertIsNone(out[h0 + timedelta(hours=1)]["dd"])

    def test_raises_after_last_attempt(self):
        def fetch(url):
            raise urllib.error.URLError("down")
        with self.assertRaises(urllib.error.URLError):
            fetch_station(42.7, 23.4, 531, "2026-01-27", "2026-01-27", fetch=fetch, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
