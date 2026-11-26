import unittest
from datetime import date, datetime, timedelta, timezone

from verification import events

UTC = timezone.utc
T = datetime(2026, 11, 3, 18, tzinfo=UTC)


def case(t0_h, lead, dec, y, y0, p=None, pt="LBSF"):
    t0 = T + timedelta(hours=t0_h)
    return {"point": pt, "t0": t0, "lead": lead, "day": t0.date(), "p": p if p is not None else (0.9 if dec else 0.05),
            "dec": dec, "y": y, "y0": y0}


class EventTests(unittest.TestCase):
    def test_onset_and_clearing(self):
        cs = [case(0, 2, True, True, False), case(1, 2, False, True, False), case(2, 2, True, False, False),
              case(3, 2, False, False, True), case(4, 2, True, True, True)]
        r = {x["lead_h"]: x for x in events.transition_scores(cs)}[2]
        self.assertEqual((r["onset"]["events"], r["onset"]["tp"], r["onset"]["fp"], r["onset"]["fn"]), (2, 1, 1, 1))
        self.assertEqual((r["clearing"]["events"], r["clearing"]["tp"]), (1, 1))     # cleared and forecast clear

    def test_episodes_warning_lead_and_false_alarm_nights(self):
        series = {"LBSF": [(T + timedelta(minutes=30 * k), 4 * 2 <= k <= 6 * 2) for k in range(0, 30)]}   # fog 22-24 UTC
        eps = events.episodes(series)
        self.assertEqual(eps["LBSF"], [(T + timedelta(hours=4), T + timedelta(hours=6))])
        warn = [case(1, 3, True, True, False)]                     # issued 19 UTC for 22 UTC: 3 h ahead
        e = events.episode_scores(warn, series)
        self.assertEqual((e["episodes"], e["warned_3h"], e["missed"]), (1, 1, 0))
        late = events.episode_scores([case(4, 1, True, True, True)], series)   # issued in fog: not a warning
        self.assertEqual(late["missed"], 1)
        # a clear second night with one alarm
        series["LBSF"] += [(T + timedelta(days=1, minutes=30 * k), False) for k in range(0, 20)]
        e2 = events.episode_scores(warn + [case(25, 2, True, False, False)], series)
        self.assertEqual((e2["clear_nights"], e2["false_alarm_nights"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
