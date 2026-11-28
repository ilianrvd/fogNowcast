import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ingest import metar_archive as M

HEADER = "station,valid,tmpf,dwpf,sknt,drct,vsby,wxcodes,metar\n"


def iem(*rows):
    return HEADER + "".join(f"{st},{valid},M,M,M,M,{vsby},M,{raw}\n" for st, valid, vsby, raw in rows)


class ParseRawTests(unittest.TestCase):
    def test_fog_report(self):
        p = M.parse_raw("LBSF 280130Z AUTO 33003KT 310V010 0600 0550N R09/0450VP2000N FZFG VV002 M02/M03 Q1007 NOSIG")
        self.assertEqual((p["vis_m"], p["rvr_m"], p["wx"], p["vv_ft"], p["cloud_base_ft"]), (600, 450, ["FZFG"], 200, None))
        self.assertEqual((p["t"], p["td"], p["qnh_hpa"], p["wind_dir"], p["wind_kt"], p["gust_kt"]), (-2, -3, 1007, 330, 3, None))
        self.assertEqual(p["clouds"], ["VV002"])

    def test_clouds_gust_and_trend_ignored(self):
        p = M.parse_raw("METAR LBBG 041500Z 27015G28KT 9999 -SHRA VCTS FEW008 SCT015CB BKN040 OVC080 12/09 Q1012 "
                        "RETS TEMPO 3000 TSRA BKN005")
        self.assertEqual((p["wind_dir"], p["wind_kt"], p["gust_kt"]), (270, 15, 28))
        self.assertEqual(p["wx"], ["-SHRA", "VCTS"])  # recent weather and trend are not taken
        self.assertEqual(p["clouds"], ["FEW008", "SCT015CB", "BKN040", "OVC080"])
        self.assertEqual(p["cloud_base_ft"], 4000)

    def test_auto_cb_without_amount_and_misspelt_trend(self):
        p = M.parse_raw("LBSF 031500Z AUTO 34005KT 9999 //////CB 24/13 Q1010 RETS NOSIG")
        self.assertEqual((p["clouds"], p["cloud_base_ft"]), (["//////CB"], None))
        p = M.parse_raw("LBGO 151500Z AUTO 13008KT 9999 -RA OVC060/// 11/11 Q0998 TEMP 5000 RA")
        self.assertEqual((p["wx"], p["cloud_base_ft"]), (["-RA"], 6000))

    def test_taf_is_dropped(self):
        self.assertTrue(M.is_taf("LBWN 282300Z 2900/2924 09008KT 7000 SCT020 BKN050"))
        self.assertFalse(M.is_taf("LBWN 282300Z 09008KT 7000 SCT020 BKN050 12/08 Q1015"))

    def test_cavok_calm_vrb(self):
        p = M.parse_raw("LBGO 011200Z 00000KT CAVOK 10/05 Q1020")
        self.assertEqual((p["vis_m"], p["clouds"], p["wind_dir"], p["wind_kt"]), (9999, ["CAVOK"], None, 0))
        p = M.parse_raw("LBWN 011200Z VRB02KT 9999 NCD M01/M07 Q1028")
        self.assertEqual((p["wind_dir"], p["wind_kt"], p["clouds"], p["t"]), (None, 2, ["NCD"], -1))

    def test_row_fog_flag_and_iem_fallback(self):
        r = M.to_row({"valid": "2026-01-28 01:30", "vsby": "0.37",
                      "metar": "LBSF 280130Z 33003KT 0999 FG M02/M03 Q1007"})
        self.assertEqual((r["time"], r["type"], r["fog"], r["low_vis"], r["vis_source"]), ("2026-01-28T01:30:00Z", "METAR", 1, 1, "metar"))
        r = M.to_row({"valid": "2026-01-28 01:30", "vsby": "0.62", "metar": "LBSF 280130Z BROKEN"})
        self.assertEqual((r["vis_m"], r["vis_source"], r["fog"]), (1000, "iem", 0))
        r = M.to_row({"valid": "2026-01-28 01:30", "vsby": "M", "metar": "LBBG 280130Z 27003KT 4200 BCFG 10/10 Q1007"})
        self.assertEqual((r["fog"], r["low_vis"]), (0, 1))   # patches are not fog


class ArchiveTests(unittest.TestCase):
    def test_type_by_minute_and_old_labels_fixed(self):
        self.assertEqual([M.report_type(t) for t in ("2026-01-28T01:00:00Z", "2026-01-28T01:30:00Z", "2026-01-28T01:07:00Z")],
                         ["METAR", "METAR", "SPECI"])
        old = [dict.fromkeys(M.FIELDS, ""), dict.fromkeys(M.FIELDS, "")]
        old[0].update(time="2026-01-28T01:30:00Z", type="SPECI", raw="A")   # written with the IEM report_type
        old[1].update(time="2026-01-28T01:00:00Z", type="METAR", raw="B")
        new = [dict(old[0], type="METAR")]
        out = M.merge(old, new)
        self.assertEqual([(r["time"][11:16], r["type"]) for r in out], [("01:00", "METAR"), ("01:30", "METAR")])

    def test_year_chunks(self):
        self.assertEqual(list(M.year_chunks("2025-12-30", "2026-01-02")),
                         [("2025-12-30", "2026-01-01"), ("2026-01-01", "2026-01-02")])

    def test_backfill_then_daily_merge_and_manifest(self):
        calls = []

        def fetch(url):
            calls.append(url)
            st = url.split("station=")[1].split("&")[0]
            if "report_type=4" in url:
                return iem((st, "2026-11-25 01:07", "0.37", f"{st} 250107Z 00000KT 0600 FG VV001 M01/M01 Q1030"))
            if st == "LBGO":
                raise OSError("timeout")
            return iem((st, "2026-10-31 23:30", "6.21", f"{st} 312330Z 27005KT 9999 NCD 05/01 Q1020"),
                       (st, "2026-11-25 01:00", "0.62", f"{st} 250100Z 00000KT 1000 BR OVC002 M01/M01 Q1030"))

        with tempfile.TemporaryDirectory() as d:
            rc = M.main(["--archive", d, "--start", "2026-10-01", "--end", "2026-11-26"], fetch=fetch, sleep=lambda s: None)
            self.assertEqual(rc, 0)
            self.assertEqual(sum("station=LBSF" in u for u in calls), 2)       # one request per type
            nov = M.read_month(M.month_path(d, "LBSF", "2026-11"))
            self.assertEqual([r["type"] for r in nov], ["METAR", "SPECI"])
            self.assertEqual(nov[1]["fog"], "1")
            man = json.loads((Path(d) / "manifest.json").read_text())
            self.assertEqual(man["period"], ["2026-10-01", "2026-11-25"])
            self.assertEqual(man["airports"]["LBSF"]["reports"], 3)
            self.assertIsNone(man["airports"]["LBGO"]["fetched_at"])            # failed airport keeps no fetch time
            self.assertIn("generated_at", man)
            sha = man["airports"]["LBSF"]["months"]["2026-10"]["sha256"]
            # daily run: same reports again -> no duplicates, unchanged files keep their sha256
            M.main(["--archive", d], fetch=fetch, sleep=lambda s: None, today=date(2026, 11, 26))
            man2 = json.loads((Path(d) / "manifest.json").read_text())
            self.assertEqual(man2["airports"]["LBSF"]["reports"], 3)
            self.assertEqual(man2["airports"]["LBSF"]["months"]["2026-10"]["sha256"], sha)
            self.assertEqual(man2["period"], ["2026-10-01", "2026-11-25"])

    def test_all_failed_writes_nothing(self):
        def fetch(url):
            raise OSError("down")
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(M.main(["--archive", d, "--start", "2026-11-01"], fetch=fetch, sleep=lambda s: None), 1)
            self.assertFalse((Path(d) / "manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
