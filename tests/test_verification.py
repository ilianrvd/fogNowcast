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
        c = {"point": "LBSF", "t0": T0, "lead": 1, "o0": dict(obs(1, 0.8, 1, fog=True), vis=300), "m0": obs(2),
             "mL": dict(obs(2), cl=80),
             "oL": obs(1, 0.9, 1, fog=True)}
        c2 = dict(c, o0=obs(1), oL=obs(1))   # NTU-like, no fog flag
        rows = core.fog_rows([c, c2], "corrected", {}, {"LBSF": (42.7, 23.4)})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1:4], (1.0, 1.0, 1))
        self.assertEqual(len(rows[0][0]), 10)                       # model terms + observation terms
        rows_m = core.fog_rows([c], "model", {}, {"LBSF": (42.7, 23.4)}, use_obs=False)
        self.assertEqual(len(rows_m[0][0]), 5)

    def test_observation_terms_learn_persistence_that_fades(self):
        X, y = [], []
        for k in range(400):
            fog0 = k % 4 == 0
            for lead in range(7):
                truth = fog0 and lead <= 2           # fog lasts 2 h, model terms carry no signal
                X.append(core.fog_features(2.0, 3.0, 0, True, fog0, 300 if fog0 else 9999, lead))
                y.append(1.0 if truth else 0.0)
        w = core.fit_logistic(X, y)
        p = lambda fog0, lead: core.predict(w, core.fog_features(2.0, 3.0, 0, True, fog0, 300 if fog0 else 9999, lead))
        self.assertGreater(p(True, 0), 0.6)
        self.assertLess(p(True, 6), p(True, 1))
        self.assertLess(p(False, 1), 0.1)
        self.assertEqual(core.vis_index(100), 0.0)
        self.assertEqual(core.vis_index(10000), 1.0)


class IceTests(unittest.TestCase):
    def test_saturation_over_ice_counts_as_saturated(self):
        for t in (-15, -10, -5, -2):
            e = core.e_ice(t)
            a = math.log(e / 6.112)
            td = 243.12 * a / (17.62 - a)                     # dew point (over water) of ice-saturated air
            self.assertGreater(t - td, 0.25)                  # rh over water < 100 %, t - td > 0
            self.assertLess(core.sat_deficit(t, td), 0.01)    # but saturated for fog
        self.assertAlmostEqual(core.sat_deficit(5.0, 4.0), 1.0)
        self.assertEqual(core.sat_deficit(-3.0, -2.0), 0.0)   # td above t is clipped
        self.assertIsNone(core.sat_deficit(None, 1.0))
        self.assertEqual(core.temp_class(-0.1), "t<0")


class RateLimitTests(unittest.TestCase):
    def test_429_waits_longer_then_succeeds(self):
        import io
        import urllib.error
        from verification.runs import fetch_run
        run = datetime(2026, 10, 10, 0, tzinfo=UTC)
        times = [(run + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(3)]
        good = {"hourly": {"time": times, "temperature_2m": [1] * 3, "dew_point_2m": [0] * 3,
                           "wind_speed_10m": [2] * 3, "cloud_cover_low": [0] * 3}}
        calls, waits = [], []

        def fetch(url):
            calls.append(url)
            if len(calls) <= 2:
                raise urllib.error.HTTPError(url, 429, "Too Many Requests", {"Retry-After": "7"} if len(calls) == 2 else {}, io.BytesIO())
            return good
        out = fetch_run(run, [{"id": "A", "lat": 1, "lon": 2, "elev_m": 3}], fetch=fetch, sleep=waits.append)
        self.assertEqual(waits, [60, 7])
        self.assertIn("A", out)

        def always429(url):
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, io.BytesIO())
        with self.assertRaises(urllib.error.HTTPError):
            fetch_run(run, [{"id": "A", "lat": 1, "lon": 2, "elev_m": 3}], fetch=always429, sleep=lambda s: None)


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
