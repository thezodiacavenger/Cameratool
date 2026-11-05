"""Smoke-test the built CameraCheck.exe (run by the Windows build workflow).

Starts the exe headless, checks ExifTool / USB (WPD) / card detection load inside the
frozen build, then decodes real sample camera files through the HTTP API.
Results are printed as GitHub annotations so they are visible on the run page.

usage: python smoke_test_exe.py path\\to\\CameraCheck.exe [sample_dir]
"""
import json
import os
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


def note(msg):
    print(f"::notice title=smoke::{msg}")


def fail(msg):
    print(f"::error title=smoke::{msg}")
    failures.append(msg)


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        body = r.read()
        return json.loads(body) if r.headers.get("Content-Type", "").startswith("application/json") else body


def post(path, data, headers=None):
    req = urllib.request.Request(BASE + path, data=data, method="POST",
                                 headers=headers or {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


env = dict(os.environ, CAMCHECK_DATA=tempfile.mkdtemp(), CAMCHECK_PORT=str(PORT))
proc = subprocess.Popen([EXE, "--no-window"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
try:
    for _ in range(60):
        try:
            status = get("/api/status")
            break
        except OSError:
            time.sleep(1)
    else:
        out = proc.stdout.read1(20000).decode("utf-8", "replace") if proc.poll() is not None else ""
        fail(f"exe did not start: {out}")
        sys.exit(1)

    note(f"status: {json.dumps(status)}")
    if not status.get("exiftool"):
        fail("bundled ExifTool not found by the exe")
    if not status.get("usb", {}).get("ok"):
        fail(f"USB (WPD/comtypes) unavailable in exe: {status.get('usb')}")
    if b"Camera Check" not in get("/"):
        fail("UI page not served")

    cards = get("/api/cards")
    note(f"card detection OK - {len(cards)} volume(s): {[c['path'] for c in cards]}")

    t0 = time.time()
    try:
        usb = post("/api/usb", b"{}")
    except urllib.error.HTTPError as ex:
        usb = {"http_error": ex.code, "body": ex.read().decode("utf-8", "replace")[:800]}
    except Exception as ex:  # noqa: BLE001 - timeouts, dropped connections
        alive = proc.poll() is None
        out = "" if alive else proc.stdout.read().decode("utf-8", "replace")[-1500:]
        usb = {"exception": repr(ex), "after_s": round(time.time() - t0, 1), "exe_alive": alive, "exe_output": out}
    note(f"usb call took {round(time.time() - t0, 1)}s")
    if usb.get("ok") or "No USB camera" in (usb.get("error") or ""):
        note(f"USB WPD enumeration ran OK: {usb.get('error') or usb.get('model')}")
    else:
        fail(f"USB readout errored: {usb}")

    if SAMPLES:
        brands = {}
        for name in SAMPLE_FILES:
            path = os.path.join(SAMPLES, name)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "rb") as fh:
                    up = post("/api/upload", fh.read(), {"X-Filename": urllib.parse.quote(name)})
                res = post("/api/analyze", json.dumps({"evidence": [up]}).encode())["results"][0]
            except urllib.error.HTTPError as ex:
                fail(f"{name}: HTTP {ex.code} {ex.read().decode('utf-8', 'replace')[:500]}")
                continue
            if res.get("error"):
                fail(f"{name}: {res['error']}")
                continue
            brands[name] = res["brand"]
            counters = ", ".join(f"{c['label']}={c['value']}" for c in res["counters"]) or "none"
            note(f"{name}: brand={res['brand']} model={res['model']} serial={res['serial']} "
                 f"counters=[{counters}] grade={res['grade']}")
            if res["brand"] in ("Unknown", "", None) and name not in ("MXF.mxf",):
                fail(f"{name}: brand not recognised")
        if brands.get("Nikon.jpg") != "Nikon":
            fail(f"Nikon sample not identified as Nikon (got {brands.get('Nikon.jpg')})")
finally:
    proc.terminate()

if failures:
    print(f"{len(failures)} smoke failure(s)")
    sys.exit(1)
print("smoke test passed")
