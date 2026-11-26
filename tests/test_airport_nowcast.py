import gzip
import json
import os
import tempfile
import unittest

from verification.airport_nowcast import collect, load_metar, score


def metar_file(d, icao, rows):
    with gzip.open(os.path.join(d, f"metar_{icao}.csv.gz"), "wt", encoding="utf-8") as f:
        f.write("time,vis_m,vis_source,wx,fog,low_vis,t,td,wind_kt,wind_dir,raw\n")
        for t, vis, fog in rows:
            f.write(f"{t},{vis},metar,{'FG' if fog else ''},{int(fog)},{int(vis < 5000)},0,0,2,0,x\n")


def table(d, name, t0, p_by_lead, stale=False):
    cols = [{"lead": "METAR", "p_lt1000": 0, "vis_m": 9999}] + [
        {"lead": f"+{h}:00", "p_lt1000": p, "vis_m": 500 if p >= 60 else 9999} for h, p in p_by_lead.items()]
    os.makedirs(os.path.join(d, "2026/11/01"), exist_ok=True)
    with gzip.open(os.path.join(d, "2026/11/01", name), "wt", encoding="utf-8") as f:
        json.dump({"airports": {"LBSF": {"t0": t0, "stale_metar": stale, "cols": cols}}}, f)


class AirportNowcastTests(unittest.TestCase):
    def test_collect_and_score(self):
        with tempfile.TemporaryDirectory() as m, tempfile.TemporaryDirectory() as t:
            rows = []
            for h in range(0, 8):
                for mm in ("00", "30"):
                    fog = h in (0, 1)                        # fog at t0 and +1 h, then clear
                    rows.append((f"2026-11-01T0{h}:{mm}:00Z", 300 if fog else 9999, fog))
            metar_file(m, "LBSF", rows)
            table(t, "table_0000.json.gz", "2026-11-01T00:00:00+00:00", {1: 80, 2: 80, 3: 10})
            table(t, "table_0030.json.gz", "2026-11-01T00:30:00+00:00", {1: 80}, stale=True)
            met = load_metar(m)
            self.assertTrue(met[("LBSF", __import__("datetime").datetime(2026, 11, 1, 0, 0, tzinfo=__import__("datetime").timezone.utc))]["fog"])
            got, tables, stale = collect(t, met)
            self.assertEqual((tables, stale, len(got)), (2, 1, 3))
            s = {r["lead_h"]: r for r in score(got, "fog")}
            self.assertEqual(s[1]["events"], 1)
            self.assertEqual(s[1]["p>=0.6"]["tp"], 1)
            self.assertEqual(s[2]["p>=0.6"]["fp"], 1)                  # forecast fog at +2 h, it cleared
            self.assertEqual(s[2]["scores_persistence"]["fp"], 1)     # persistence is wrong too
            self.assertEqual(s[3]["brier"], 0.01)


if __name__ == "__main__":
    unittest.main()
