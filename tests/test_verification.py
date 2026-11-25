import math
import unittest
from datetime import datetime, timedelta, timezone

from verification import core
from verification.runs import parse

UTC = timezone.utc
T0 = datetime(2026, 10, 10, 6, tzinfo=UTC)


def obs(t, td=None, ws=2.0, fog=None):
    td = t - 1 if td is None else td
    o = {"t": t, "td": td, "dd": t - td, "ws": ws}
    if fog is not None:
        o["fog"] = fog
    return o


class CoreTests(unittest.TestCase):
    def test_usable_run_respects_latency(self):
        runs = [T0 - timedelta(hours=6), T0]
        self.assertEqual(core.usable_run(T0, runs), T0 - timedelta(hours=6))
        self.assertEqual(core.usable_run(T0 + timedelta(hours=4), runs), T0)
        self.assertIsNone(core.usable_run(T0 - timedelta(hours=7), runs))

    def make(self, tau_true, days=10):
        """Model has a residual r at t0 that decays with tau_true in the truth."""
        obsd, model, runs = {}, {}, []
        for d in range(days):
            run = T0 + timedelta(days=d) - timedelta(hours=6)
            runs.append(run)
            model[run] = {"P": {}}
            for h in range(0, 17):
                v = run + timedelta(hours=h)
                model[run]["P"][v] = obs(10.0)
            t0 = run + timedelta(hours=6)
            r = -3.0 + 0.2 * (d % 3)
            for lead in range(7):
                obsd[("P", t0 + timedelta(hours=lead))] = obs(10.0 + r * math.exp(-lead / tau_true))
        t0s = [T0 + timedelta(days=d) for d in range(days)]
        return core.build_cases(t0s, obsd, model, runs, ["P"])

    def test_corrected_beats_model_and_persistence_and_tau_is_found(self):
        cases = self.make(tau_true=3)
        self.assertEqual(len(cases), 70)
        self.assertEqual(core.fit_tau(cases, "t"), 3)
        e = {m: core.mae(core.abs_errors(cases, "t", m, 3)[3]) for m in ("model", "persistence", "corrected")}
        self.assertAlmostEqual(e["corrected"], 0.0, places=6)
        self.assertGreater(e["model"], 0.5)
        self.assertGreater(e["persistence"], 0.5)
        self.assertIsNone(core.fit_tau(self.make(tau_true=1e9), "t"))   # no decay -> None (keep residual)

    def test_bootstrap_interval_sign(self):
        cases = self.make(tau_true=3)
        a, b = core.paired([c for c in cases if c["lead"] == 3], "t", "corrected", "model")[3]
        lo, hi = core.bootstrap_diff(a, b)
        self.assertLess(hi, 0)

    def test_wind_and_deficit_not_negative(self):
        c = {"lead": 0, "o0": obs(5, ws=0.0), "m0": obs(5, ws=4.0), "mL": obs(5, ws=1.0), "oL": obs(5)}
        self.assertEqual(core.forecast(c, "ws", "corrected"), 0.0)

    def test_logistic_separates_and_contingency(self):
        X = [core.fog_features(dd, 1.0, 50, True) for dd in (0.1, 0.2, 0.3, 0.2)] + \
            [core.fog_features(dd, 6.0, 0, False) for dd in (4, 5, 6, 7, 8, 9)]
        y = [1, 1, 1, 1, 0, 0, 0, 0, 0, 0]
        w = core.fit_logistic(X, y)
        ps = [core.predict(w, x) for x in X]
        self.assertGreater(min(ps[:4]), max(ps[4:]))
        s = core.contingency(ps, y, 0.5)
        self.assertEqual((s["tp"], s["fp"], s["fn"]), (4, 0, 0))
        self.assertLess(core.brier(ps, y), 0.1)

    def test_fog_rows_only_with_metar_flag(self):
        c = {"point": "LBSF", "t0": T0, "lead": 1, "o0": obs(1, 0.8, 1, fog=True), "m0": obs(2), "mL": dict(obs(2), cl=80),
             "oL": obs(1, 0.9, 1, fog=True)}
        c2 = dict(c, o0=obs(1), oL=obs(1))   # NTU-like, no fog flag
        rows = core.fog_rows([c, c2], "corrected", 3, {"LBSF": (42.7, 23.4)})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:4], (1.0, 1.0, 1))


class RunsParseTests(unittest.TestCase):
    def test_multi_location_and_lead_window(self):
        run = datetime(2026, 10, 10, 0, tzinfo=UTC)
        times = [(run + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(20)]  # leads 0..19
        loc = lambda base: {"hourly": {"time": times, "temperature_2m": [base] * 20, "dew_point_2m": [base - 1] * 20,
                                       "wind_speed_10m": [3] * 20, "cloud_cover_low": [10] * 20}}
        out = parse([loc(1.0), loc(5.0)], [{"id": "A"}, {"id": "B"}], run)
        self.assertEqual(len(out["A"]), 17)                 # leads 0..16
        self.assertEqual(out["B"][run]["dd"], 1.0)
        with self.assertRaises(RuntimeError):
            parse([loc(1.0)], [{"id": "A"}, {"id": "B"}], run)


if __name__ == "__main__":
    unittest.main()
