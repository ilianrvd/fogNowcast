import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from calibration import skill
from calibration.build_metar import airport_stations, write_reports
from calibration.metar_iem import fetch_reports, parse_report, parse_vis_m
from correction.hourly import slot_obs

UTC = timezone.utc


def row(valid, metar, wx="M", vsby="M", tmpf="32", dwpf="32"):
    return {"valid": valid, "metar": metar, "wxcodes": wx, "vsby": vsby, "tmpf": tmpf, "dwpf": dwpf,
            "sknt": "3", "drct": "330"}


class MetarParseTests(unittest.TestCase):
    def test_visibility_from_raw_metar(self):
        self.assertEqual(parse_vis_m("LBSF 280130Z AUTO 33003KT 310V010 0600 0550N FZFG M02/M03 Q1007", "0.37"), (600, "metar"))
        self.assertEqual(parse_vis_m("LBPD 041900Z AUTO 00000KT 0750 0700NE FG VV002 M00/M00 Q1013", "0.47"), (750, "metar"))
        self.assertEqual(parse_vis_m("LBGO 011200Z VRB02KT 9999 NCD 10/05 Q1020", "6.21"), (9999, "metar"))
        self.assertEqual(parse_vis_m("LBGO 011200Z 27010G20KT CAVOK 10/05 Q1020", "6.21"), (9999, "metar"))
        # no usable raw report: IEM statute miles rounded to 100 m
        self.assertEqual(parse_vis_m("", "0.62"), (1000, "iem"))
        self.assertEqual(parse_vis_m("", "M"), (None, None))

    def test_fog_needs_fg_token_and_vis_below_1000(self):
        mk = lambda raw, wx: parse_report(row("2026-01-28 01:30", raw, wx))
        self.assertTrue(mk("LBSF 280130Z 33003KT 0600 FZFG M02/M03 Q1007", "FZFG")["fog"])
        self.assertTrue(mk("LBSF 280130Z 33003KT 0999 FG M02/M03 Q1007", "FG")["fog"])
        self.assertFalse(mk("LBSF 280130Z 33003KT 1000 FG M02/M03 Q1007", "FG")["fog"])     # 1000 m is not < 1000
        self.assertFalse(mk("LBBG 160230Z 27003KT 4200 BCFG 10/10 Q1007", "BCFG")["fog"])   # patches are not fog
        self.assertFalse(mk("LBBG 160230Z 27003KT 0800 VCFG 10/10 Q1007", "VCFG")["fog"])
        low = mk("LBBG 160230Z 27003KT 4200 BCFG 10/10 Q1007", "BCFG")
        self.assertTrue(low["low_vis"])

    def test_units_and_retry(self):
        r = parse_report(row("2026-01-28 01:30", "LBSF 280130Z 33003KT 0600 FZFG M02/M03 Q1007", "FZFG", tmpf="28.4", dwpf="26.6"))
        self.assertEqual((r["t"], r["td"]), (-2.0, -3.0))
        calls = []

        def fetch(url):
            calls.append(url)
            if len(calls) == 1:
                raise OSError("timeout")
            return "station,valid,tmpf,dwpf,sknt,drct,vsby,wxcodes,metar\nLBSF,2026-01-28 01:30,28.4,26.6,3,330,0.37,FZFG,LBSF 280130Z 33003KT 0600 FZFG M02/M03 Q1007\n"
        out = fetch_reports("LBSF", "2026-01-27", "2026-11-25", fetch=fetch, sleep=lambda s: None)
        self.assertEqual(len(out), 1)
        self.assertIn("report_type=3", calls[0])
        with self.assertRaises(RuntimeError):
            fetch_reports("LBSF", "2026-01-27", "2026-11-25", fetch=lambda u: "garbage", sleep=lambda s: None)


class SkillTests(unittest.TestCase):
    def test_metar_slots_take_worst_report(self):
        t0 = datetime(2026, 1, 28, 1, 30, tzinfo=UTC)
        mk = lambda dt, vis, fog: {"time": t0 + timedelta(minutes=dt), "vis_m": vis, "fog": fog, "low_vis": vis < 5000}
        slots = skill.metar_by_slot([mk(0, 4000, False), mk(8, 600, True), mk(30, 9999, False)])
        self.assertTrue(slots[t0]["fog"])                                  # SPECI at +8 min lands in the same slot
        self.assertFalse(slots[t0 + timedelta(minutes=30)]["low_vis"])

    def test_scores_and_any_station(self):
        t0 = datetime(2026, 1, 28, 0, 0, tzinfo=UTC)
        slots, obs = {}, {}
        for i in range(40):
            s = t0 + timedelta(minutes=30 * i)
            fog = i < 12
            slots[s] = {"fog": fog, "low_vis": fog, "vis_m": 500 if fog else 9999}
            obs[("A", s)] = {"rh": 98.0 if (fog and i < 9) else 70.0, "dd": 0.3 if fog else 5.0}   # A catches 9 of 12
            obs[("B", s)] = {"rh": 98.0 if (fog and i >= 6) else 70.0, "dd": 0.3 if fog else 5.0}  # B catches 6 of 12
        rows = skill.evaluate_airport(slots, obs, ["A", "B"], 42.7, 23.4)
        get = lambda st, p, tg="fog", sub="all": next(r for r in rows if r["station"] == st and r["predictor"] == p
                                                       and r["target"] == tg and r["subset"] == sub)
        a = get("A", "rh>=95")
        self.assertEqual((a["tp"], a["fn"], a["fp"], a["tn"]), (9, 3, 0, 28))
        self.assertEqual(a["pod"], 0.75)
        self.assertEqual(a["far"], 0.0)
        any_ = get("any", "rh>=95")
        self.assertEqual(any_["tp"], 12)                                   # the union catches everything
        self.assertEqual(any_["csi"], 1.0)
        self.assertEqual(get("A", "dd<=1")["pod"], 1.0)
        self.assertEqual(skill.top_rows(rows, "fog", "all")[0]["csi"], 1.0)
        self.assertIn("night", {r["subset"] for r in rows})                # 00-06 UTC in January is night
        self.assertEqual(get("A", "rh>=95", sub="cold")["n"], 40)

    def test_events_below_minimum_not_ranked(self):
        rows = [{"target": "fog", "subset": "all", "events": 3, "csi": 1.0, "far": 0.0}]
        self.assertEqual(skill.top_rows(rows, "fog", "all"), [])


class SlotObsTests(unittest.TestCase):
    def test_half_hour_slots(self):
        import gzip
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "ntu_2026-01.csv.gz"
            with gzip.open(p, "wt", encoding="utf-8") as f:
                f.write("time,id,t,rh,td,t_surface,pressure_hpa,wind_kt,wind_dir,flags\n")
                for hhmm, t in (("00:00", 1), ("00:15", 2), ("00:30", 3), ("00:45", 4), ("01:00", 5)):
                    f.write(f"2026-01-28T{hhmm}:00Z,NTU:1,{t}.0,90,0.0,,950,5,90,\n")
            obs = slot_obs([p], ["NTU:1"], step_min=30)
            g = lambda h, m: obs[("NTU:1", datetime(2026, 1, 28, h, m, tzinfo=UTC))]["t"]
            self.assertEqual((g(0, 0), g(0, 30), g(1, 0)), (1.0, 3.0, 5.0))   # exact records win over +-15 min ones


class StationGroupsTests(unittest.TestCase):
    def test_airport_stations(self):
        sj = {"stations": [{"id": "NTU:1", "groups": ["airport:LBSF"]}, {"id": "NTU:2", "groups": ["road_fog"]},
                           {"id": "NTU:3", "groups": ["airport:LBSF", "road_fog"]}]}
        self.assertEqual(airport_stations(sj), {"LBSF": ["NTU:1", "NTU:3"]})

    def test_write_reports_roundtrip(self):
        import csv, gzip
        r = parse_report(row("2026-01-28 01:30", "LBSF 280130Z 33003KT 0600 FZFG M02/M03 Q1007", "FZFG"))
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.csv.gz"
            write_reports(p, [r])
            rows = list(csv.DictReader(gzip.open(p, "rt", encoding="utf-8")))
            self.assertEqual((rows[0]["vis_m"], rows[0]["fog"]), ("600", "1"))


if __name__ == "__main__":
    unittest.main()
