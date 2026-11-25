import unittest
from datetime import datetime, timezone

from tools.health_check import evaluate

NOW = datetime(2026, 11, 25, 20, 30, tzinfo=timezone.utc)
OK_BR = {"archive-live": "2026-11-25T20:20:30Z", "archive-fallback": "2026-11-25T20:05:30Z"}


def latest(age_gen="2026-11-25T20:20:27Z", n=95, flag=None, k=0):
    st = [{"qc": {"flags": [flag] if (flag and i < k) else []}} for i in range(n)]
    return {"generated_at": age_gen, "stations": st}


STATE = {"last_request_at": "2026-11-25T20:20:01Z"}


class HealthTests(unittest.TestCase):
    def test_all_ok(self):
        self.assertEqual(evaluate(NOW, latest(), STATE, OK_BR), [])

    def test_stale_file_and_stopped_runs(self):
        p = evaluate(NOW, latest("2026-11-25T19:30:00Z"), {"last_request_at": "2026-11-25T19:30:00Z"}, OK_BR)
        self.assertEqual(len(p), 2)
        self.assertIn("60 мин", p[0])

    def test_unreadable_and_few_stations(self):
        self.assertEqual(len(evaluate(NOW, None, None, OK_BR)), 2)
        self.assertTrue(any("10 станции" in x for x in evaluate(NOW, latest(n=10), STATE, OK_BR)))

    def test_flag_shares(self):
        self.assertEqual(evaluate(NOW, latest(flag="late", k=40), STATE, OK_BR), [])
        p = evaluate(NOW, latest(flag="fallback_unknown", k=60), STATE, OK_BR)
        self.assertTrue(any("fallback_unknown" in x for x in p))

    def test_old_or_missing_branch(self):
        p = evaluate(NOW, latest(), STATE, {"archive-live": None, "archive-fallback": "2026-11-25T18:00:00Z"})
        self.assertEqual(len(p), 2)


if __name__ == "__main__":
    unittest.main()
