import random
import unittest
from datetime import datetime, timedelta, timezone

from verification import events, onset
from verification.build import parse_clouds

H = timedelta(hours=1)
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def synthetic(days=42, seed=2):
    """One airport. Fog forms at 03 UTC on every third night; before it the deficit falls and the wind drops."""
    rnd = random.Random(seed)
    obs, series = {}, {}
    for d in range(days):
        fog_night = d % 3 == 0
        for h in range(24):
            t = T0 + timedelta(days=d, hours=h)
            hh = t.hour
            fog = fog_night and 3 <= hh <= 6
            if fog_night and 0 <= hh < 3:
                dd, ws = 1.5 - 0.5 * hh, 2.0
            elif fog:
                dd, ws = 0.0, 1.0
            else:
                dd, ws = 4.0 + rnd.random(), 8.0 + rnd.random()
            obs[("LBSF", t)] = {"t": 5.0, "td": 5.0 - dd, "ws": ws, "fog": fog, "vis": 300 if fog else 8000,
                                "low_cover": 0.0, "ceil_ft": None}
            series[t] = {"t": 5.0, "td": 5.0 - dd, "ws": ws, "cl": 0}
    runs = sorted({(T0 + timedelta(hours=6 * k)) for k in range(-2, days * 4)})
    model = {r: {"LBSF": series} for r in runs}
    return obs, model, runs


class Onset(unittest.TestCase):
    def setUp(self):
        self.obs, self.model, self.runs = synthetic()
        hours = [T0 + i * H for i in range(42 * 24)]
        self.rows = onset.samples(self.obs, self.model, self.runs, ["LBSF"], {"LBSF": (42.7, 23.4)}, {}, hours)

    def test_samples_only_clear_t0_and_windows(self):
        self.assertTrue(self.rows)
        for s in self.rows:
            self.assertFalse(self.obs[("LBSF", s["t0"])]["fog"])
            self.assertFalse(self.obs[("LBSF", s["t0"] - H)]["fog"])
            self.assertEqual(len(s["x"]), len(onset.FEATURES))
        self.assertEqual({s["window"] for s in self.rows}, {"1-3", "3-6"})
        pos = [s for s in self.rows if s["window"] == "1-3" and s["y"]]
        self.assertTrue(pos)
        self.assertTrue(all(s["t0"].hour in (0, 1, 2) for s in pos))   # fog at 03-06 seen from 00-02
        self.assertEqual(pos[0]["f"]["ntu_missing"], 1.0)

    def test_window_target_needs_metar(self):
        obs = {k: v for k, v in self.obs.items() if not (k[1].day == 2 and k[1].hour in (2, 3))}
        self.assertIsNone(onset.window_target(obs, "LBSF", T0 + timedelta(days=1), 1, 3))
        self.assertTrue(onset.window_target(self.obs, "LBSF", T0, 1, 3))

    def test_methods_and_stop_criterion(self):
        fog_series = {"LBSF": [(t, o["fog"]) for (p, t), o in self.obs.items()]}
        eps = events.episodes(fog_series)
        res = onset.run_all(self.rows, eps)
        w = res["1-3"]
        for m in ("rule", "climatology", "logistic"):
            self.assertIn("csi", w[m])
        self.assertGreater(w["logistic"]["csi"], 0.5)          # the synthetic signal is learnt out of sample
        self.assertGreaterEqual(w["logistic"]["episodes"]["warned_2h_share"], 0.5)
        self.assertTrue(w["logistic"]["passes"])
        self.assertIn("logistic_weights", w)
        md = onset.report({"period": ["a", "b"], "episodes": 14, "windows": res})
        self.assertIn("Стоп-условие", md)

    def test_episode_warning_lead(self):
        rows = [{"point": "LBSF", "t0": T0 + 1 * H}, {"point": "LBSF", "t0": T0 + 2 * H}]
        eps = {"LBSF": [(T0 + 3 * H, T0 + 6 * H)]}
        r = onset.episode_warnings(rows, [True, True], eps, "1-3")
        self.assertEqual((r["warned_2h"], r["missed"]), (1, 0))
        r = onset.episode_warnings(rows, [False, True], eps, "1-3")
        self.assertEqual(r["warned_2h"], 0)

    def test_parse_clouds(self):
        self.assertEqual(parse_clouds("LBSF 270000Z 00000KT 0200 FG VV001 M01/M01 Q1030"), (1.0, 100))
        self.assertEqual(parse_clouds("LBSF 270000Z 00000KT CAVOK 05/01 Q1020"), (0.0, None))
        self.assertEqual(parse_clouds("LBSF 270000Z 27003KT 9999 SCT040 BKN080 05/01 Q1020")[0], 0.0)


if __name__ == "__main__":
    unittest.main()
