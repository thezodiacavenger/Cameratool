"""Smoke-test the built CameraCheck.exe (run by the Windows build workflow).

Starts the exe headless and drives it exactly like the app window does (session cookie +
token): checks ExifTool / USB (WPD) / card detection inside the frozen build, decodes
real sample camera files, saves a record, downloads every export and verifies the
report-card certificate. Results are printed as GitHub annotations.

usage: python smoke_test_exe.py path\\to\\CameraCheck.exe [sample_dir]
"""
import http.cookiejar
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

EXE = sys.argv[1]
SAMPLES = sys.argv[2] if len(sys.argv) > 2 else None
PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"
SAMPLE_FILES = ["Nikon.jpg", "NikonD70.jpg", "NikonD2Hs.jpg", "Nikon.nef", "Canon.jpg", "Canon1DmkIII.jpg",
                "CanonRaw.cr3", "CanonRaw.cr2", "Sony.jpg", "FujiFilm.jpg", "FujiFilm.raf", "Pentax.jpg",
                "Olympus.jpg", "Panasonic.jpg", "Panasonic.rw2", "Red.r3d", "MXF.mxf", "PhaseOne.iiq"]
failures = []
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
TOKEN = [""]


def note(msg):
    print(f"::notice title=smoke::{msg}")


def fail(msg):
    print(f"::error title=smoke::{msg}")
    failures.append(msg)


def get(path, raw=False):
    with opener.open(BASE + path, timeout=60) as r:
        body = r.read()
        if raw:
            return r, body
        return json.loads(body) if r.headers.get("Content-Type", "").startswith("application/json") else body


def post(path, data, headers=None):
    h = {"Content-Type": "application/json", "X-CC-Token": TOKEN[0]}
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=data, method="POST", headers=h)
    with opener.open(req, timeout=120) as r:
        return json.loads(r.read())


env = dict(os.environ, CAMCHECK_DATA=tempfile.mkdtemp(), CAMCHECK_PORT=str(PORT))
proc = subprocess.Popen([EXE, "--no-window"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    for _ in range(60):
        try:
            page = get("/")
            break
        except OSError:
            time.sleep(1)
    else:
        out = proc.stdout.read1(20000).decode("utf-8", "replace") if proc.poll() is not None else ""
        fail(f"exe did not start: {out}")
        sys.exit(1)
    m = re.search(rb'name="cc-token" content="([^"]+)"', page)
    TOKEN[0] = m.group(1).decode() if m else ""
    if not TOKEN[0] or b"Camera Check" not in page:
        fail("UI page / session token not served")

    status = get("/api/status")
    note(f"status: {json.dumps(status)}")
    if not status.get("exiftool"):
        fail("bundled ExifTool not found by the exe")
    if not status.get("usb", {}).get("ok"):
        fail(f"USB (WPD/comtypes) unavailable in exe: {status.get('usb')}")

    # security: no cookie -> refused
    try:
        urllib.request.urlopen(BASE + "/api/records", timeout=10)
        fail("API answered without a session cookie")
    except urllib.error.HTTPError as ex:
        if ex.code != 403:
            fail(f"expected 403 without session, got {ex.code}")

    cards = get("/api/cards")
    note(f"card detection OK - {len(cards)} volume(s): {[c['path'] for c in cards]}")

    t0 = time.time()
    try:
        usb = post("/api/usb", b"{}")
    except urllib.error.HTTPError as ex:
        usb = {"http_error": ex.code, "body": ex.read().decode("utf-8", "replace")[:800]}
    except Exception as ex:  # noqa: BLE001 - timeouts, dropped connections
        usb = {"exception": repr(ex), "after_s": round(time.time() - t0, 1), "exe_alive": proc.poll() is None}
    if usb.get("ok") or "No USB camera" in (usb.get("error") or ""):
        note(f"USB WPD enumeration ran OK in {round(time.time() - t0, 1)}s: {usb.get('error') or usb.get('model')}")
    else:
        fail(f"USB readout errored: {usb}")

    if SAMPLES:
        brands, lines, decoded = {}, [], {}
        for name in SAMPLE_FILES:
            path = os.path.join(SAMPLES, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as fh:
                    up = post("/api/upload", fh.read(), {"X-Filename": urllib.parse.quote(name),
                                                         "Content-Type": "application/octet-stream"})
                res = post("/api/analyze", json.dumps({"evidence": [up]}).encode())["results"][0]
            except urllib.error.HTTPError as ex:
                fail(f"{name}: HTTP {ex.code} {ex.read().decode('utf-8', 'replace')[:500]}")
                continue
            if res.get("error"):
                fail(f"{name}: {res['error']}")
                continue
            brands[name] = res["brand"]
            decoded[name] = (res, up)
            counters = ", ".join(f"{c['label']}={c['value']}" for c in res["counters"]) or "none"
            lines.append(f"{name}: {res.get('display_name')} serial={res['serial']} counters=[{counters}] grade={res['grade']}")
            if res["brand"] in ("Unknown", "", None) and name not in ("MXF.mxf",):
                fail(f"{name}: brand not recognised")
        note("decoded samples%0A" + "%0A".join(lines))
        if brands.get("Nikon.jpg") != "Nikon":
            fail(f"Nikon sample not identified as Nikon (got {brands.get('Nikon.jpg')})")

        # save a record from a real decode and pull every export
        res, up = decoded.get("Nikon.nef") or next(iter(decoded.values()))
        rec = {"brand": res["brand"], "model": res["model"], "serial": res["serial"], "label_serial": res["serial"],
               "serial_match": "MATCH", "shutter_count": (res.get("primary") or {}).get("value"),
               "rated_life": 150000, "life_used_pct": 2.4, "grade": "A - Excellent",
               "functional": "Autofocus: Pass; Card slots: Pass", "analysis": {"results": [res]}, "evidence": [up],
               "extra": {"display_name": res.get("display_name")}}
        saved = post("/api/records", json.dumps(rec).encode())
        rid = saved["id"]
        checks = [(f"/card/{rid}", b"Camera Condition Report Card"), (f"/report/{rid}", b"Used Camera Evaluation"),
                  (f"/api/records/{rid}/card.docx", b"PK"), (f"/api/records/{rid}/report.docx", b"PK"),
                  ("/api/export.xlsx", b"PK"), ("/api/export.csv", "Record".encode()), ("/api/backup.zip", b"PK")]
        for path, marker in checks:
            try:
                r, body = get(path, raw=True)
                if marker not in body[:200000]:
                    fail(f"{path}: unexpected content")
            except urllib.error.HTTPError as ex:
                fail(f"{path}: HTTP {ex.code}")
        v = post("/api/verify", json.dumps({"id": rid, "code": saved["cert_code"]}).encode())
        if not v.get("valid"):
            fail(f"certificate did not verify: {v}")
        note(f"record {rid} saved; report card, Word, Excel, CSV and backup exports OK; certificate {saved['cert_code']} verified")
finally:
    proc.terminate()

if failures:
    print(f"{len(failures)} smoke failure(s)")
    sys.exit(1)
print("smoke test passed")
