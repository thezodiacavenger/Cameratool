"""Local web server + desktop window launcher. Stdlib only; binds to 127.0.0.1."""
import datetime as _dt
import html
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, analyzer, drives, exiftool, usb
from .store import Store

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
IMAGE_EXT = re.compile(r"\.(jpe?g|nef|nrw|cr2|cr3|crw|arw|srf|sr2|raf|orf|ori|rw2|rwl|pef|dng|"
                       r"3fr|fff|iiq|x3f|srw|hif|heic|heif|r3d|mxf|mov|mp4|crm|braw)$", re.I)

STORE = None
CUSTOM_RATINGS = None
LAST_SEEN = [time.time()]


def custom_ratings_path():
    return os.path.join(STORE.base, "ratings_custom.json")


def ratings():
    return analyzer.load_ratings(custom_ratings_path())


def analyze_paths(items):
    """items: list of (path, display_name, evidence_id)."""
    raws = exiftool.read_metadata([p for p, _, _ in items])
    by_path = {os.path.normcase(os.path.abspath(r.get("SourceFile", ""))): r for r in raws}
    rs = ratings()
    out = []
    for path, name, eid in items:
        raw = by_path.get(os.path.normcase(os.path.abspath(path)))
        if raw is None and len(raws) == len(items):
            raw = raws[items.index((path, name, eid))]
        if raw is None:
            out.append({"file": name, "error": "ExifTool could not read this file"})
            continue
        r = analyzer.analyze(raw, rs, display_name=name)
        r["evidence_id"] = eid
        r["history"] = STORE.history_for_serial(r.get("serial"))
        if r["history"] and r.get("primary"):
            last = [h for h in r["history"] if h.get("shutter_count")]
            if last and last[-1]["shutter_count"] > r["primary"]["value"]:
                r["flags"].append({"level": "fail", "msg":
                    f"This serial was logged on {last[-1]['created_at']} with a HIGHER count "
                    f"({last[-1]['shutter_count']:,}). Counter reset or board swap - investigate."})
        out.append(r)
    return {"results": out, "cross_flags": analyzer.cross_check(out)}


def group_by_body(results):
    """Cards often hold files from several bodies - keep the newest file per body."""
    groups = {}
    for r in results:
        if r.get("error"):
            continue
        key = (r.get("model") or "?", r.get("serial") or "?")
        groups.setdefault(key, []).append(r)
    bodies = []
    for (model, serial), rs in groups.items():
        rs.sort(key=lambda r: (r.get("captured_at") or "", (r.get("primary") or {}).get("value", 0)), reverse=True)
        stills = [r for r in rs if r.get("primary")] or rs
        best = dict(stills[0])
        best["files_from_body"] = len(rs)
        best["flags"] = list(best["flags"]) + analyzer.cross_check(rs)
        bodies.append(best)
    bodies.sort(key=lambda r: r.get("captured_at") or "", reverse=True)
    return bodies


def scan_folder(path):
    if not os.path.isdir(path):
        raise ValueError(f"Folder not found: {path}")
    photos, clips, xmls = drives.newest_files(path)
    items = [(p, os.path.basename(p), None) for p in photos + clips]
    out = {"results": [], "cross_flags": []}
    if items:
        out = analyze_paths(items)
    results = out["results"]
    for x in xmls:
        meta = drives.sony_nrt(x)
        if meta and meta.get("model"):
            raw = {"SourceFile": x, "File:FileType": "XML", "XML:Make": meta.get("make") or "Sony",
                   "XML:Model": meta["model"], "XML:SerialNumber": meta.get("serial")}
            r = analyzer.analyze(raw, ratings(), display_name=meta["file"])
            r["evidence_id"] = None
            r["history"] = STORE.history_for_serial(r.get("serial"))
            results.append(r)
    if not results:
        raise ValueError("No camera files found on this card / folder.")
    bodies = group_by_body(results)
    flags = []
    if len(bodies) > 1:
        flags.append({"level": "info", "msg": f"This card holds files from {len(bodies)} different cameras. "
                      "Each body is shown separately - evaluate the one whose serial matches the label."})
    errors = [r for r in results if r.get("error")]
    return {"results": bodies + errors, "cross_flags": flags, "scanned": len(items) + len(xmls), "path": path}


def usb_readout():
    r = usb.read_camera(STORE.save_bytes)
    img = r.get("image")
    if img:
        try:
            a = analyze_paths([(img["path"], img["name"], img["evidence_id"])])
            r["analysis"] = a
            res = a["results"][0] if a["results"] else None
            if res and not res.get("error"):
                if img.get("partial"):
                    res["flags"].append({"level": "info", "msg": "Read the first part of a large RAW file from the "
                                         "camera - enough for the MakerNotes."})
                if r.get("serial") and res.get("serial") and not analyzer.compare_serial(r["serial"], res["serial"]):
                    res["flags"].append({"level": "fail", "msg": f"Serial reported over USB ({r['serial']}) differs "
                                         f"from the serial in the newest photo ({res['serial']}). The photo on the "
                                         "card was not taken by this body."})
                fc = (res.get("primary") or {}).get("value")
                if r.get("shutter_count") and fc and fc > r["shutter_count"] + 5:
                    res["flags"].append({"level": "warn", "msg": "File counter is higher than the live USB counter."})
        except Exception as ex:  # noqa: BLE001
            r.setdefault("errors", []).append(f"decode: {ex}")
    for k in ("path",):
        (img or {}).pop(k, None)
    return r


def status():
    cmd, ver = exiftool.find_exiftool()
    return {"version": __version__, "exiftool": ver, "exiftool_cmd": " ".join(cmd) if cmd else None,
            "usb": usb.backend(), "data_dir": STORE.base, "platform": sys.platform}


# ------------------------------------------------------------------ report --

def render_report(rec):
    e = lambda v: html.escape("" if v is None else str(v))
    a = rec.get("analysis") or {}
    results = a.get("results", []) if isinstance(a, dict) else []
    usbr = a.get("usb") if isinstance(a, dict) else None
    rows = [("Record", rec["id"]), ("Date", rec["created_at"]), ("Staff", rec.get("staff")),
            ("PO / trade-in #", rec.get("reference")), ("Seller", rec.get("seller")),
            ("Brand / model", f"{rec.get('brand') or ''} {rec.get('model') or ''}"),
            ("Serial (from camera)", rec.get("serial")), ("Serial (body label)", rec.get("label_serial")),
            ("Serial match", rec.get("serial_match")),
            ("Shutter / actuation count", f"{rec['shutter_count']:,}" if rec.get("shutter_count") else "-"),
            ("Count source", rec.get("count_source")),
            ("Rated life", f"{rec['rated_life']:,}" if rec.get("rated_life") else "-"),
            ("Life used", f"{rec['life_used_pct']}%" if rec.get("life_used_pct") is not None else "-"),
            ("Shutter grade", rec.get("grade")),
            ("Runtime hours", rec.get("runtime_hours")), ("Sensor hours", rec.get("sensor_hours")),
            ("Cosmetic", rec.get("cosmetic")), ("Functional", rec.get("functional")),
            ("Offer", rec.get("offer")), ("Notes", rec.get("notes"))]
    body = "".join(f"<tr><th>{e(k)}</th><td>{e(v) if v not in (None, '') else '-'}</td></tr>" for k, v in rows)
    flags = "".join(f"<li class='{e(f['level'])}'><b>{e(f['level'].upper())}</b> {e(f['msg'])}</li>"
                    for f in rec.get("flags") or [])
    files = ""
    for r in results:
        cs = "".join(f"<li>{e(c['label'])}: <b>{c['value']:,}</b> <small>({e(c['tag'])}, {e(c['reliability'])})</small></li>"
                     for c in r.get("counters", []))
        files += (f"<h3>{e(r.get('file'))}</h3><p>{e(r.get('make'))} {e(r.get('model'))} · captured "
                  f"{e(r.get('captured_at'))} · firmware {e(r.get('firmware'))}</p><ul>{cs or '<li>No counters in file</li>'}</ul>")
    if usbr and usbr.get("ok"):
        cs = "".join(f"<li>{e(k)}: <b>{v:,}</b></li>" for k, v in (usbr.get("counters") or {}).items())
        img = usbr.get("image") or {}
        files += (f"<h3>USB readout ({e(usbr.get('transport'))})</h3><p>{e(usbr.get('manufacturer'))} {e(usbr.get('model'))}"
                  f" · serial {e(usbr.get('serial'))} · firmware {e(usbr.get('firmware'))}</p>"
                  f"<ul>{cs or '<li>No live counter exposed over USB</li>'}"
                  f"{('<li>Newest photo downloaded from camera: ' + e(img.get('name')) + '</li>') if img else ''}</ul>")
    imgs = "".join(
        f"<figure><img src='/evidence/{e(ev['id'])}/{urllib.parse.quote(ev['name'])}'><figcaption>{e(ev['name'])}</figcaption></figure>"
        for ev in rec.get("evidence") or [] if re.search(r"\.(jpe?g|png|heic|webp)$", ev.get("name", ""), re.I))
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Camera evaluation {e(rec['id'])}</title>
<style>body{{font:14px/1.45 system-ui,Segoe UI,Helvetica,Arial;margin:32px;color:#111;background:#fff}}
table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;border-bottom:1px solid #ddd;padding:6px 8px;vertical-align:top}}
th{{width:220px;color:#555;font-weight:600}}li.fail{{color:#b00020}}li.warn{{color:#8a5a00}}li.info{{color:#335}}
figure{{display:inline-block;margin:8px}}img{{max-width:320px;max-height:240px;border:1px solid #ccc}}
.btn{{position:fixed;top:16px;right:16px}}@media print{{.btn{{display:none}}}}</style></head><body>
<button class=btn onclick=print()>Print / Save PDF</button>
<h1>Used Camera Evaluation</h1><table>{body}</table>
<h2>Checks</h2><ul>{flags or '<li>No issues flagged</li>'}</ul>
<h2>Source files</h2>{files or '<p>Manual entry only.</p>'}
{('<h2>Evidence</h2>' + imgs) if imgs else ''}
<p style="margin-top:40px;color:#666">Evaluator signature: ______________________ &nbsp; Generated by Camera Check {__version__}</p>
</body></html>"""


# ----------------------------------------------------------------- handler --

class Handler(BaseHTTPRequestHandler):
    server_version = "CameraCheck"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        return json.loads(self._body or b"{}")

    def _local_only(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            self._send(403, {"error": "forbidden"})
            return False
        if self.command == "POST" or self.command == "DELETE":
            origin = self.headers.get("Origin")
            if origin and not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", origin):
                self._send(403, {"error": "bad origin"})
                return False
        return True

    def do_GET(self):
        LAST_SEEN[0] = time.time()
        if not self._local_only():
            return
        u = urllib.parse.urlparse(self.path)
        p = u.path
        q = urllib.parse.parse_qs(u.query)
        try:
            if p in ("/", "/index.html"):
                return self._file(os.path.join(WEB, "index.html"))
            if p.startswith("/static/"):
                return self._file(os.path.join(WEB, os.path.basename(p)))
            if p == "/api/status":
                return self._send(200, status())
            if p == "/api/guides":
                return self._send(200, analyzer.load_guides())
            if p == "/api/ratings":
                return self._send(200, ratings())
            if p == "/api/records":
                return self._send(200, STORE.list(q.get("q", [""])[0]))
            m = re.match(r"^/api/records/([\w-]+)$", p)
            if m:
                r = STORE.get(m.group(1))
                return self._send(200 if r else 404, r or {"error": "not found"})
            m = re.match(r"^/report/([\w-]+)$", p)
            if m:
                r = STORE.get(m.group(1))
                return self._send(200, render_report(r), "text/html; charset=utf-8") if r else self._send(404, "not found", "text/plain")
            if p == "/api/export.csv":
                return self._send(200, STORE.export_csv(), "text/csv; charset=utf-8",
                                  {"Content-Disposition": "attachment; filename=camera_evaluations.csv"})
            m = re.match(r"^/evidence/([0-9a-f]{32})/(.+)$", p)
            if m:
                path = STORE.evidence_path(m.group(1), os.path.basename(urllib.parse.unquote(m.group(2))))
                return self._file(path)
            if p == "/api/cards":
                return self._send(200, drives.list_cards())
            if p == "/api/ping":
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except Exception as ex:  # noqa: BLE001 - report to UI
            self._send(500, {"error": str(ex)})

    def do_POST(self):
        LAST_SEEN[0] = time.time()
        if not self._local_only():
            return
        p = urllib.parse.urlparse(self.path).path
        # Always consume the request body: on Windows, closing a socket with unread
        # data resets the connection before the client receives the response.
        self._body = b""
        if p != "/api/upload":
            self._body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            if p == "/api/upload":
                name = urllib.parse.unquote(self.headers.get("X-Filename") or "upload.bin")
                n = int(self.headers.get("Content-Length") or 0)
                eid, safe, path = STORE.save_upload(name, self.rfile, n)
                return self._send(200, {"id": eid, "name": safe})
            if p == "/api/analyze":
                body = self._json()
                items = []
                for ev in body.get("evidence", []):
                    path = STORE.evidence_path(ev["id"], os.path.basename(ev["name"]))
                    if os.path.isfile(path):
                        items.append((path, ev["name"], ev["id"]))
                if not items:
                    return self._send(400, {"error": "no files"})
                return self._send(200, analyze_paths(items))
            if p == "/api/scan":
                return self._send(200, scan_folder(self._json().get("path", "").strip().strip('"')))
            if p == "/api/usb":
                return self._send(200, usb_readout())
            if p == "/api/records":
                body = self._json()
                rid = STORE.save(body)
                return self._send(200, {"id": rid})
            if p == "/api/ratings":
                body = self._json()
                path = custom_ratings_path()
                data = {"models": []}
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as fh:
                        data = json.load(fh)
                re.compile(body["match"])
                data["models"] = [m for m in data["models"] if m["match"] != body["match"]]
                if not body.get("delete"):
                    data["models"].insert(0, {"brand": body.get("brand", ""), "name": body.get("name") or body["match"],
                                              "match": body["match"], "rated": int(body["rated"]) if body.get("rated") else None,
                                              "electronic_only": bool(body.get("electronic_only")),
                                              "confidence": "store"})
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=1)
                return self._send(200, {"ok": True})
            if p == "/api/exiftool/recheck":
                exiftool.reset_cache()
                return self._send(200, status())
            self._send(404, {"error": "not found"})
        except Exception as ex:  # noqa: BLE001
            self._send(500, {"error": str(ex)})

    def do_DELETE(self):
        if not self._local_only():
            return
        m = re.match(r"^/api/records/([\w-]+)$", urllib.parse.urlparse(self.path).path)
        if m:
            STORE.delete(m.group(1))
            return self._send(200, {"ok": True})
        self._send(404, {"error": "not found"})

    def _file(self, path):
        if not os.path.isfile(path):
            return self._send(404, "not found", "text/plain")
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)


# ---------------------------------------------------------------- launcher --

def _free_port(preferred=8765):
    for port in (preferred, 0):
        s = socket.socket()
        if os.name != "nt":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            port = s.getsockname()[1]
            s.close()
            return port
        except OSError:
            s.close()
    return 0


def open_window(url):
    """Open as a chromeless desktop window (Edge/Chrome app mode), else default browser."""
    cands = []
    if os.name == "nt":
        pf = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        for base in pf:
            cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                      os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        cands += [shutil.which(n) or "" for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    profile = os.path.join(STORE.base, "window-profile")
    for c in cands:
        if c and os.path.isfile(c):
            try:
                subprocess.Popen([c, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1320,900",
                                  "--no-first-run", "--no-default-browser-check"])
                return
            except OSError:
                continue
    webbrowser.open(url)


def main(argv=None):
    global STORE
    argv = argv if argv is not None else sys.argv[1:]
    STORE = Store()
    port = _free_port(int(os.environ.get("CAMCHECK_PORT", "8765")))
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Camera Check {__version__} running at {url}")
    print(f"Data folder: {STORE.base}")
    cmd, ver = exiftool.find_exiftool()
    print(f"ExifTool: {ver or 'NOT FOUND - see README'}")
    if "--no-window" not in argv:
        threading.Timer(0.6, open_window, args=(url,)).start()
    print("Close this window (or press Ctrl+C) to quit.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
