import datetime as dt
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from camcheck import analyzer  # noqa: E402

with open(os.path.join(os.path.dirname(__file__), "fixtures", "samples.json")) as fh:
    S = json.load(fh, object_pairs_hook=dict)

NOW = dt.datetime(2026, 11, 5, 12, 0)
A = lambda k: analyzer.analyze(S[k], now=NOW)
levels = lambda r: {f["level"] for f in r["flags"]}


class T(unittest.TestCase):
    def test_nikon_mechanical_preferred(self):
        r = A("nikon_z6ii")
        self.assertEqual(r["brand"], "Nikon")
        self.assertEqual(r["primary"]["value"], 41006)
        self.assertEqual(r["rating"]["rated"], 200000)
        self.assertEqual(r["life_used_pct"], 20.5)
        self.assertTrue(r["grade"].startswith("B"))
        self.assertEqual(r["serial"], "6012345")
        self.assertNotIn("fail", levels(r))

    def test_sony(self):
        r = A("sony_a7iii")
        self.assertEqual(r["primary"]["value"], 152330)
        self.assertEqual(len([c for c in r["counters"] if c["label"].startswith("Shutter count (")]), 1)
        self.assertEqual(r["life_used_pct"], 76.2)
        self.assertEqual(r["serial"], "04567890")

    def test_canon_r5_and_dslr(self):
        r = A("canon_r5")
        self.assertEqual(r["primary"]["value"], 88412)
        self.assertEqual(r["power_on_time"], "2026:11:04 17:58:00")
        d = A("canon_5d4")
        self.assertIsNone(d["primary"])
        self.assertIn("warn", levels(d))
        self.assertIn("USB", d["guide"]["usb"])

    def test_fuji_low_conf(self):
        r = A("fuji_xt4")
        self.assertEqual(r["primary"]["value"], 21011)
        self.assertEqual(r["primary"]["reliability"], "low")

    def test_olympus_guide(self):
        r = A("olympus_em1")
        self.assertEqual(r["brand"], "OM System")
        self.assertIsNone(r["primary"])
        self.assertIn("INFO", r["guide"]["menu"])

    def test_edited_and_stale(self):
        r = A("edited")
        msgs = " ".join(f["msg"] for f in r["flags"])
        self.assertIn("MakerNotes are missing", msgs)
        self.assertIn("processed by software", msgs)
        self.assertIn("days ago", msgs)

    def test_na_value(self):
        self.assertIsNone(A("nikon_na")["primary"])

    def test_red(self):
        r = A("red")
        self.assertEqual(r["brand"], "RED")
        self.assertEqual(r["serial"], "VR123456")
        self.assertNotIn("fail", levels(r))
        self.assertIn("runtime", r["guide"]["menu"])

    def test_filetype_brand_and_serial_cleanup(self):
        self.assertEqual(A("red_nomodel")["brand"], "RED")
        self.assertEqual(A("nikon_old_serial")["serial"], "20025585")

    def test_cross_check(self):
        a, b = A("nikon_z6ii"), dict(A("nikon_z6ii"))
        b["serial"] = "999"
        self.assertTrue(any(f["level"] == "fail" for f in analyzer.cross_check([a, b])))
        c = dict(a); c["primary"] = dict(a["primary"], value=10); c["captured_at"] = "2026-11-05 11:00:00"
        self.assertTrue(any("backwards" in f["msg"] for f in analyzer.cross_check([a, c])))

    def test_serial_compare(self):
        self.assertTrue(analyzer.compare_serial("6012345", "6012345"))
        self.assertTrue(analyzer.compare_serial("032021001234", "21001234"))
        self.assertFalse(analyzer.compare_serial("6012345", "6012399"))

    def test_brand_norm(self):
        for make, exp in [("OM Digital Solutions", "OM System"), ("LEICA CAMERA AG", "Leica"),
                          ("Hasselblad", "Hasselblad"), ("RICOH IMAGING COMPANY, LTD.", "Pentax"),
                          ("Panasonic", "Panasonic"), ("FUJIFILM", "Fujifilm")]:
            self.assertEqual(analyzer.normalize_brand(make), exp)

    def test_ratings_regex_valid(self):
        import re
        for m in analyzer.load_ratings():
            re.compile(m["match"])
        self.assertEqual(analyzer.find_rating("NIKON Z 9", analyzer.load_ratings())["electronic_only"], True)
        self.assertEqual(analyzer.find_rating("Canon EOS R6 Mark II", analyzer.load_ratings())["rated"], 300000)
        self.assertEqual(analyzer.find_rating("Canon EOS R6", analyzer.load_ratings())["rated"], 300000)
        self.assertEqual(analyzer.find_rating("Canon EOS R5 Mark II", analyzer.load_ratings())["name"], "EOS R5 Mark II")
        # Canon writes the short form into files (e.g. a real R6 Mark II CR3 says "Canon EOS R6m2")
        self.assertEqual(analyzer.find_rating("Canon EOS R6m2", analyzer.load_ratings())["name"], "EOS R6 Mark II")
        self.assertEqual(analyzer.find_rating("Canon EOS R5m2", analyzer.load_ratings())["name"], "EOS R5 Mark II")
        self.assertEqual(analyzer.find_rating("Canon EOS R6", analyzer.load_ratings())["name"], "EOS R6")

    def test_canon_r6m2_file(self):
        raw = {"SourceFile": "_HAR8472.CR3", "File:FileType": "CR3", "IFD0:Make": "Canon",
               "IFD0:Model": "Canon EOS R6m2", "ExifIFD:DateTimeOriginal": "2026:11:05 10:00:00",
               "Canon:SerialNumber": "123456789012", "Canon:ShutterCount": 30000}
        r = analyzer.analyze(raw, now=NOW)
        self.assertEqual(r["display_name"], "Canon EOS R6 Mark II")
        self.assertEqual((r["primary"]["value"], r["rating"]["rated"], r["life_used_pct"]), (30000, 300000, 10.0))
        self.assertTrue(r["grade"].startswith("A"))


if __name__ == "__main__":
    unittest.main()
