"""v2 tests: store migrations, certificates, exports, report cards and HTTP security."""
import http.client
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from camcheck import ooxml, reportcard, server  # noqa: E402
from camcheck.store import Store, csv_safe, safe_filename  # noqa: E402

try:
    import openpyxl  # noqa: F401
    import docx  # noqa: F401
    HAVE_OFFICE_LIBS = True
except ImportError:
    HAVE_OFFICE_LIBS = False

REC = {"brand": "Nikon", "model": "NIKON Z 6_2", "serial": "6012345", "label_serial": "6012345",
       "serial_match": "MATCH", "shutter_count": 41006, "rated_life": 200000, "life_used_pct": 20.5,
       "grade": "B - Very good", "count_source": "File: Mechanical shutter count [Nikon:MechanicalShutterCount] (high)",
       "functional": "Autofocus: Pass; Card slots: Fail; Wi-Fi / Bluetooth: N/A", "cosmetic": "Very good (normal wear)",
       "offer": "1450", "seller": "Jane Q", "reference": "PO-1", "notes": "internal secret note", "staff": "Emmet",
       "flags": [{"level": "info", "msg": "x"}],
       "analysis": {"results": [{"serial": "6012345", "firmware": "1.60", "lens": "NIKKOR Z 24-70mm f/4 S",
                                 "rating": {"rated": 200000, "confidence": "manufacturer"}}]}}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Store(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrates_v1_database(self):
        with tempfile.TemporaryDirectory() as d:
            con = sqlite3.connect(os.path.join(d, "records.sqlite"))
            con.execute("""CREATE TABLE records (id TEXT PRIMARY KEY, created_at TEXT, staff TEXT, reference TEXT,
                seller TEXT, brand TEXT, model TEXT, serial TEXT, label_serial TEXT, serial_match TEXT,
                shutter_count INTEGER, count_source TEXT, rated_life INTEGER, life_used_pct REAL, grade TEXT,
                runtime_hours REAL, sensor_hours REAL, cosmetic TEXT, functional TEXT, offer TEXT, notes TEXT,
                flags TEXT, analysis TEXT, evidence TEXT)""")
            con.execute("INSERT INTO records (id, created_at, brand, model, shutter_count, flags, analysis, evidence) "
                        "VALUES ('20260101-000000-abcd', '2026-01-01 00:00:00', 'Sony', 'ILCE-7M3', 5, '[]', '{}', '[]')")
            con.commit()
            con.close()
            s = Store(d)
            r = s.get("20260101-000000-abcd")
            self.assertEqual((r["model"], r["status"], r["extra"]), ("ILCE-7M3", "Evaluated", {}))
            self.assertTrue(s.verify(r["id"], s.cert_code(r))["valid"])
            Store(d)  # re-opening an up-to-date DB is a no-op

    def test_certificate_and_tamper(self):
        rid = self.s.save(REC)
        rec = self.s.get(rid)
        self.assertRegex(rec["cert_code"], r"^[A-Z2-7]{4}-[A-Z2-7]{4}-[A-Z2-7]{4}$")
        self.assertTrue(self.s.verify(rid, rec["cert_code"].lower().replace("-", " "))["valid"])
        self.assertFalse(self.s.verify(rid, "AAAA-AAAA-AAAA")["valid"])
        self.assertFalse(self.s.verify("nope", rec["cert_code"])["valid"])
        c = sqlite3.connect(self.s.db_path)
        c.execute("UPDATE records SET shutter_count=100 WHERE id=?", (rid,))
        c.commit()
        c.close()
        self.assertFalse(self.s.verify(rid, rec["cert_code"])["valid"])

    def test_update_rules(self):
        rid = self.s.save(REC)
        code = self.s.get(rid)["cert_code"]
        self.s.update(rid, {"status": "Purchased", "notes": "n", "shutter_count": 1})  # count not editable
        r = self.s.get(rid)
        self.assertEqual((r["status"], r["shutter_count"], r["cert_code"]), ("Purchased", 41006, code))
        self.s.update(rid, {"runtime_hours": "12.5"})
        self.assertNotEqual(self.s.get(rid)["cert_code"], code)
        self.s.update(rid, {"status": "Hacked"})
        self.assertEqual(self.s.get(rid)["status"], "Evaluated")

    def test_csv_injection_and_bom(self):
        self.s.save(dict(REC, seller="=HYPERLINK(\"http://x\")", notes="+1"))
        out = self.s.export_csv()
        self.assertTrue(out.startswith("﻿"))
        self.assertIn("'=HYPERLINK", out)
        self.assertEqual(csv_safe("-2"), "'-2")
        self.assertEqual(csv_safe(5), 5)

    def test_paths_and_names(self):
        self.assertEqual(safe_filename("../../evil\n-x.jpg"), "evil_-x.jpg")
        self.assertEqual(safe_filename("--rm.jpg"), "rm.jpg")
        self.assertEqual(safe_filename(""), "upload.bin")
        with self.assertRaises(ValueError):
            self.s.evidence_path("../../etc")
        rid = self.s.save(dict(REC, evidence=[{"id": "../x", "name": "a"}, {"id": "a" * 32, "name": "../p.jpg"}]))
        self.assertEqual(self.s.get(rid)["evidence"], [{"id": "a" * 32, "name": "p.jpg"}])
        self.assertIsNone(self.s.get("../../x"))

    def test_settings_and_key(self):
        st = self.s.save_settings({"store_name": "Vistek Calgary", "bogus": 1,
                                   "usage_profiles": [["Pro", "50000"], ["Bad", 0]]})
        self.assertEqual(st["store_name"], "Vistek Calgary")
        self.assertNotIn("bogus", st)
        self.assertEqual(st["usage_profiles"], [["Pro", 50000]])
        with self.assertRaises(ValueError):
            self.s.set_key("short")
        k = self.s.key()
        self.assertEqual(k, self.s.key())

    def test_backup(self):
        self.s.save(REC)
        z = zipfile.ZipFile(io.BytesIO(self.s.backup_zip()))
        self.assertIn("records.sqlite", z.namelist())
        self.assertNotIn("signing.key", z.namelist())


class ExportTests(unittest.TestCase):
    def test_card_content_excludes_internal(self):
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            rec = s.get(s.save(REC))
            card = reportcard.build_card(rec, s.settings())
            self.assertEqual((card["remaining"], card["left_pct"], card["letter"]), (158994, 79.5, "B"))
            self.assertEqual(card["usage"][1], {"profile": "Enthusiast", "per_year": 10000, "years": 15.9})
            self.assertEqual(card["name"], "Nikon Z 6II")
            self.assertIn("Nikon Z 6II · s/n 6012345", reportcard.card_html(card))
            self.assertTrue(card["verifications"][0]["ok"])
            page = reportcard.card_html(card)
            for secret in ("1450", "Jane Q", "PO-1", "internal secret note"):
                self.assertNotIn(secret, page)
            self.assertIn(rec["cert_code"], page)
            self.assertNotIn("onclick", page)
            raw = reportcard.card_docx(card)
            xml = zipfile.ZipFile(io.BytesIO(raw)).read("word/document.xml").decode()
            self.assertIn("shutter life remaining", xml)
            for secret in ("1450", "Jane Q", "internal secret note"):
                self.assertNotIn(secret, xml)
            internal = reportcard.internal_docx(rec, s.settings())
            self.assertIn("internal secret note", zipfile.ZipFile(io.BytesIO(internal)).read("word/document.xml").decode())

    def test_card_variants(self):
        st = Store(tempfile.mkdtemp()).settings()
        eo = reportcard.build_card(dict(REC, id="X", life_used_pct=None, rated_life=None, grade=None,
                                        analysis={"results": [{"rating": {"electronic_only": True}}]}), st)
        self.assertTrue(eo["electronic_only"])
        self.assertIn("electronic shutter", reportcard.card_html(eo))
        cine = reportcard.build_card({"id": "C", "brand": "RED", "model": "V-RAPTOR", "runtime_hours": 1234.5,
                                      "sensor_hours": 800.0, "serial": "VR1"}, st)
        h = reportcard.card_html(cine)
        self.assertIn("1,234.5 h", h)
        reportcard.card_docx(cine)

    @unittest.skipUnless(HAVE_OFFICE_LIBS, "openpyxl/python-docx not installed")
    def test_office_files_open(self):
        import docx
        import openpyxl
        with tempfile.TemporaryDirectory() as d:
            s = Store(d)
            rid = s.save(REC)
            head, rows = s.export_rows()
            data = ooxml.xlsx([("Evaluations", head, rows)])
            p = os.path.join(d, "x.xlsx")
            with open(p, "wb") as fh:
                fh.write(data)
            ws = openpyxl.load_workbook(p).active
            self.assertEqual(ws["A1"].value, "Record / certificate ID")
            self.assertEqual(ws["A2"].value, rid)
            p2 = os.path.join(d, "c.docx")
            with open(p2, "wb") as fh:
                fh.write(reportcard.card_docx(reportcard.build_card(s.get(rid), s.settings())))
            text = "\n".join(par.text for par in docx.Document(p2).paragraphs)
            self.assertIn("Camera Condition Report Card", text)


class NamingTests(unittest.TestCase):
    def test_display_names(self):
        from camcheck.analyzer import display_name
        self.assertEqual(display_name("Canon", "Canon EOS R5"), "Canon EOS R5")
        self.assertEqual(display_name("Nikon", "NIKON Z 6_2", {"name": "Z 6II"}), "Nikon Z 6II")
        self.assertEqual(display_name("Nikon", "NIKON D7500", {"name": "D7500 / D7200 / D7100 / D7000"}), "Nikon D7500")
        self.assertEqual(display_name("Sony", "ILCE-7M3", {"name": "Alpha 7 III"}), "Sony Alpha 7 III")
        self.assertEqual(display_name("RED", "V-RAPTOR"), "RED V-RAPTOR")

    def test_low_confidence_caveat(self):
        st = Store(tempfile.mkdtemp()).settings()
        fuji = dict(REC, brand="Fujifilm", model="X-T4", count_source="File: Image count [FujiFilm:ImageCount] (low)")
        c = reportcard.build_card(dict(fuji, id="F"), st)
        self.assertIn("lower bound", c["caveat"])
        self.assertIn("(estimate)", reportcard.card_html(c))
        self.assertEqual(reportcard.build_card(dict(REC, id="N"), st)["caveat"], "")


class HTTPSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        server.STORE = Store(cls.tmp.name)
        server.STORE.save(REC)
        cls.httpd = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.tmp.cleanup()

    def req(self, method, path, body=None, headers=None, host=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"127.0.0.1:{self.port}"}
        h.update(headers or {})
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r, data

    def cookie(self):
        return {"Cookie": f"cc_session={server.SESSION}"}

    def write(self):
        return dict(self.cookie(), **{"X-CC-Token": server.SESSION, "Content-Type": "application/json"})

    def test_index_sets_cookie_and_csp(self):
        r, data = self.req("GET", "/")
        self.assertEqual(r.status, 200)
        self.assertIn("HttpOnly", r.getheader("Set-Cookie"))
        self.assertIn("SameSite=Strict", r.getheader("Set-Cookie"))
        self.assertIn("script-src 'self'", r.getheader("Content-Security-Policy"))
        self.assertIn(server.SESSION.encode(), data)

    def test_requires_session(self):
        self.assertEqual(self.req("GET", "/api/records")[0].status, 403)
        self.assertEqual(self.req("GET", "/api/records", headers=self.cookie())[0].status, 200)
        self.assertEqual(self.req("GET", "/api/backup.zip")[0].status, 403)

    def test_dns_rebinding_blocked(self):
        self.assertEqual(self.req("GET", "/", host="evil.example:8765")[0].status, 403)

    def test_csrf(self):
        body = json.dumps({"status": "Declined"})
        rid = server.STORE.list()[0]["id"]
        self.assertEqual(self.req("POST", f"/api/records/{rid}", body, self.cookie())[0].status, 403)
        bad = dict(self.write(), Origin="https://evil.example")
        self.assertEqual(self.req("POST", f"/api/records/{rid}", body, bad)[0].status, 403)
        cross = dict(self.write(), **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(self.req("POST", f"/api/records/{rid}", body, cross)[0].status, 403)
        r, data = self.req("POST", f"/api/records/{rid}", body, self.write())
        self.assertEqual(r.status, 200, data)
        self.assertEqual(json.loads(data)["status"], "Declined")

    def test_uploaded_html_is_not_served_as_page(self):
        h = dict(self.write(), **{"X-Filename": "evil.html", "Content-Type": "application/octet-stream"})
        r, data = self.req("POST", "/api/upload", b"<script>alert(1)</script>", h)
        ev = json.loads(data)
        r, _ = self.req("GET", f"/evidence/{ev['id']}/{ev['name']}", headers=self.cookie())
        self.assertEqual(r.getheader("Content-Type"), "application/octet-stream")
        self.assertIn("attachment", r.getheader("Content-Disposition"))

    def test_exports_and_pages(self):
        rid = server.STORE.list()[0]["id"]
        for path, ctype in ((f"/card/{rid}", "text/html"), (f"/report/{rid}", "text/html"),
                            (f"/api/records/{rid}/card.docx", "wordprocessingml"),
                            (f"/api/records/{rid}/report.docx", "wordprocessingml"),
                            ("/api/export.xlsx", "spreadsheetml"), ("/api/export.csv", "text/csv"),
                            (f"/api/records/{rid}/summary", "text/plain"), ("/api/backup.zip", "zip")):
            r, data = self.req("GET", path, headers=self.cookie())
            self.assertEqual(r.status, 200, path)
            self.assertIn(ctype, r.getheader("Content-Type"), path)
        self.assertEqual(self.req("GET", "/card/..%2F..%2Fx", headers=self.cookie())[0].status, 404)

    def test_verify_endpoint(self):
        rec = server.STORE.get(server.STORE.list()[0]["id"])
        r, data = self.req("POST", "/api/verify", json.dumps({"id": rec["id"], "code": rec["cert_code"]}), self.write())
        self.assertTrue(json.loads(data)["valid"])

    def test_static_whitelist(self):
        self.assertEqual(self.req("GET", "/static/app.js")[0].status, 200)
        self.assertEqual(self.req("GET", "/static/../server.py")[0].status, 404)
        self.assertEqual(self.req("GET", "/static/index.html")[0].status, 404)


if __name__ == "__main__":
    unittest.main()
