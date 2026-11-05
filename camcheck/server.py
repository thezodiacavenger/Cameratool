"""Local web server + desktop window launcher. Stdlib only; binds to 127.0.0.1.

Security model (the app is only reachable from this PC):
  * listens on 127.0.0.1 only; requests with any other Host header are refused (DNS rebinding)
  * a random per-launch session secret is set as an HttpOnly, SameSite=Strict cookie when the
    app window loads; every other endpoint requires it
  * state-changing requests additionally need the secret in an X-CC-Token header and a
    same-origin Origin header (CSRF)
  * strict Content-Security-Policy (no inline script), nosniff, no framing
  * uploaded files are never served as active content
"""
import datetime as _dt
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __app_name__, __publisher__, __version__, analyzer, drives, exiftool, ooxml, reportcard, usb
from .store import EID_RE, MAX_UPLOAD, RID_RE, STATUSES, Store

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
STATIC_FILES = {"app.js", "style.css", "report.js", "icon.svg"}
GITHUB_REPO = "thezodiacavenger/Cameratool"
SIGNATURE = "CameraCheck-local-v2"

STORE = None
SESSION = secrets.token_urlsafe(32)
LAST_SEEN = [time.time()]
SEEN_CLIENT = [False]

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
       "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


def custom_ratings_path():
    return os.path.join(STORE.base, "ratings_custom.json")


def ratings():
    return analyzer.load_ratings(custom_ratings_path())


# ---------------------------------------------------------------- analysis --

def analyze_paths(items):
    """items: list of (path, display_name, evidence_id)."""
    raws = exiftool.read_metadata([p for p, _, _ in items])
    by_path = {os.path.normcase(os.path.abspath(r.get("SourceFile", ""))): r for r in raws}
    rs = ratings()
    out = []
    for idx, (path, name, eid) in enumerate(items):
        raw = by_path.get(os.path.normcase(os.path.abspath(path)))
        if raw is None and len(raws) == len(items):
            raw = raws[idx]
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
        groups.setdefault((r.get("model") or "?", r.get("serial") or "?"), []).append(r)
    bodies = []
    for rs in groups.values():
        rs.sort(key=lambda r: (r.get("captured_at") or "", (r.get("primary") or {}).get("value", 0)), reverse=True)
        stills = [r for r in rs if r.get("primary")] or rs
        best = dict(stills[0])
        best["files_from_body"] = len(rs)
        best["flags"] = list(best["flags"]) + analyzer.cross_check(rs)
        bodies.append(best)
    bodies.sort(key=lambda r: r.get("captured_at") or "", reverse=True)
    return bodies


def scan_folder(path):
    path = os.path.abspath(os.path.expanduser(str(path).strip().strip('"')))
    if not path or not os.path.isdir(path):
        raise ValueError(f"Folder not found: {path}")
    photos, clips, xmls = drives.newest_files(path)
    items = [(p, os.path.basename(p), None) for p in photos + clips]
    out = analyze_paths(items) if items else {"results": [], "cross_flags": []}
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
        img.pop("path", None)
    return r


def status():
    cmd, ver = exiftool.find_exiftool()
    return {"version": __version__, "publisher": __publisher__, "exiftool": ver,
            "exiftool_cmd": " ".join(cmd) if cmd else None, "usb": usb.backend(),
            "data_dir": STORE.base, "platform": sys.platform, "statuses": STATUSES}


def _vtuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0")[:3])


def update_check():
    req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "CameraCheck"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read(200_000))
    latest = str(data.get("tag_name") or "").lstrip("v")
    url = str(data.get("html_url") or "")
    if not url.startswith(f"https://github.com/{GITHUB_REPO}/"):
        url = f"https://github.com/{GITHUB_REPO}/releases"
    return {"current": __version__, "latest": latest, "url": url,
            "newer": _vtuple(latest) > _vtuple(__version__)}


def export_xlsx(q="", status_filter=""):
    recs = STORE.list(q, status_filter) if (q or status_filter) else STORE.all()
    head, rows = STORE.export_rows(recs)
    by = {}
    for r in recs:
        b = by.setdefault(r.get("brand") or "Unknown", {"n": 0, "pcts": [], "purchased": 0, "mismatch": 0})
        b["n"] += 1
        if r.get("life_used_pct") is not None:
            b["pcts"].append(r["life_used_pct"])
        b["purchased"] += r.get("status") == "Purchased"
        b["mismatch"] += r.get("serial_match") == "MISMATCH"
    summary = [[k, v["n"], round(sum(v["pcts"]) / len(v["pcts"]), 1) if v["pcts"] else None, v["purchased"], v["mismatch"]]
               for k, v in sorted(by.items(), key=lambda kv: -kv[1]["n"])]
    widths = [22, 18, 14, 24, 14, 16, 22, 12, 24, 16, 16, 12, 13, 12, 11, 20, 10, 10, 18, 20, 40, 12, 40, 18, 50, 40]
    return ooxml.xlsx([("Evaluations", head, rows, widths),
                       ("By brand", ["Brand", "Evaluations", "Avg life used %", "Purchased", "Serial mismatches"],
                        summary, [20, 14, 16, 12, 18])],
                      title="Camera Check evaluations", creator=f"{STORE.settings()['store_name']} - Camera Check")


# ----------------------------------------------------------------- handler --

class Handler(BaseHTTPRequestHandler):
    server_version = "CameraCheck"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    # -- responses ------------------------------------------------------------
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, default=str)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _download(self, data, filename, ctype):
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", filename)
        self._send(200, data, ctype, {"Content-Disposition": f'attachment; filename="{safe}"'})

    def _json(self):
        try:
            data = json.loads(self._body or b"{}")
        except ValueError:
            raise ValueError("Invalid JSON")
        if not isinstance(data, dict):
            raise ValueError("Expected a JSON object")
        return data

    # -- security gates -------------------------------------------------------
    def _host_ok(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        return host in ("127.0.0.1", "localhost")

    def _session_ok(self):
        cookies = self.headers.get("Cookie") or ""
        m = re.search(r"(?:^|;\s*)cc_session=([A-Za-z0-9_-]+)", cookies)
        return bool(m) and hmac.compare_digest(m.group(1), SESSION)

    def _write_ok(self):
        origin = self.headers.get("Origin")
        if origin and not re.match(r"^http://(127\.0\.0\.1|localhost):\d+$", origin):
            return False
        site = self.headers.get("Sec-Fetch-Site")
        if site and site not in ("same-origin", "none"):
            return False
        tok = self.headers.get("X-CC-Token") or ""
        return hmac.compare_digest(tok, SESSION)

    def _gate(self, path, write=False):
        if not self._host_ok():
            self._send(403, {"error": "forbidden"})
            return False
        public = path in ("/", "/index.html", "/api/hello", "/favicon.ico") or path.startswith("/static/")
        if public and not write:
            return True
        if not self._session_ok() or (write and not self._write_ok()):
            self._send(403, {"error": "Session expired - close this window and reopen Camera Check."})
            return False
        return True

    def _touch(self):
        LAST_SEEN[0] = time.time()
        SEEN_CLIENT[0] = True

    # -- GET ------------------------------------------------------------------
    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        q = urllib.parse.parse_qs(u.query)
        if not self._gate(p):
            return
        self._touch()
        try:
            if p in ("/", "/index.html"):
                with open(os.path.join(WEB, "index.html"), encoding="utf-8") as fh:
                    page = fh.read().replace("{{TOKEN}}", SESSION).replace("{{VERSION}}", __version__)
                return self._send(200, page, "text/html; charset=utf-8", {
                    "Set-Cookie": f"cc_session={SESSION}; HttpOnly; SameSite=Strict; Path=/"})
            if p == "/api/hello":
                return self._send(200, {"app": SIGNATURE, "version": __version__})
            if p == "/favicon.ico":
                return self._file(os.path.join(WEB, "icon.svg"), "image/svg+xml")
            if p.startswith("/static/"):
                name = p[len("/static/"):]
                if name not in STATIC_FILES:
                    return self._send(404, {"error": "not found"})
                return self._file(os.path.join(WEB, name))
            if p == "/api/status":
                return self._send(200, status())
            if p == "/api/guides":
                return self._send(200, analyzer.load_guides())
            if p == "/api/ratings":
                return self._send(200, ratings())
            if p == "/api/settings":
                s = STORE.settings()
                s["signing_key_hint"] = STORE.key()[:4] + "…"
                return self._send(200, s)
            if p == "/api/stats":
                return self._send(200, STORE.stats())
            if p == "/api/records":
                return self._send(200, STORE.list(q.get("q", [""])[0][:100], q.get("status", [""])[0][:40]))
            m = re.match(r"^/api/records/([0-9A-Za-z-]+)(?:/(summary|card|report\.docx|card\.docx))?$", p)
            if m:
                rec = STORE.get(m.group(1))
                if not rec:
                    return self._send(404, {"error": "Record not found"})
                kind = m.group(2)
                st = STORE.settings()
                label = re.sub(r"[^A-Za-z0-9]+", "_", f"{rec.get('brand') or ''}_{rec.get('model') or ''}").strip("_")
                if kind is None:
                    rec["summary"] = reportcard.summary_text(rec)
                    return self._send(200, rec)
                if kind == "summary":
                    return self._send(200, reportcard.summary_text(rec), "text/plain; charset=utf-8")
                if kind == "card":
                    return self._send(200, reportcard.build_card(rec, st))
                if kind == "card.docx":
                    return self._download(reportcard.card_docx(reportcard.build_card(rec, st), STORE.logo_path()),
                                          f"Report_card_{label}_{rec['id']}.docx", DOCX)
                return self._download(reportcard.internal_docx(rec, st, STORE.logo_path()),
                                      f"Evaluation_{label}_{rec['id']}.docx", DOCX)
            m = re.match(r"^/(report|card)/([0-9A-Za-z-]+)$", p)
            if m:
                rec = STORE.get(m.group(2))
                if not rec:
                    return self._send(404, "Record not found", "text/plain; charset=utf-8")
                st = STORE.settings()
                page = (reportcard.internal_html(rec, st) if m.group(1) == "report"
                        else reportcard.card_html(reportcard.build_card(rec, st)))
                return self._send(200, page, "text/html; charset=utf-8")
            if p == "/api/export.csv":
                return self._download(STORE.export_csv().encode("utf-8"),
                                      f"camera_evaluations_{_dt.date.today()}.csv", "text/csv; charset=utf-8")
            if p == "/api/export.xlsx":
                return self._download(export_xlsx(q.get("q", [""])[0][:100], q.get("status", [""])[0][:40]),
                                      f"camera_evaluations_{_dt.date.today()}.xlsx", XLSX)
            if p == "/api/backup.zip":
                return self._download(STORE.backup_zip(), f"CameraCheck_backup_{_dt.date.today()}.zip", "application/zip")
            m = re.match(r"^/evidence/([0-9a-f]{32})/([^/]+)$", p)
            if m:
                return self._evidence(m.group(1), urllib.parse.unquote(m.group(2)))
            if p == "/logo.png":
                return self._file(STORE.logo_path(), "image/png")
            if p == "/api/cards":
                return self._send(200, drives.list_cards())
            if p == "/api/update-check":
                return self._send(200, update_check())
            if p == "/api/ping":
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except Exception as ex:  # noqa: BLE001 - report to UI
            self._send(500, {"error": str(ex)})

    # -- POST -----------------------------------------------------------------
    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        streaming = p in ("/api/upload", "/api/logo")
        # Always consume the request body: on Windows, closing a socket with unread
        # data resets the connection before the client receives the response.
        self._body = b""
        if not streaming:
            self._body = self.rfile.read(min(n, 4 * 1024 * 1024))
        if not self._gate(p, write=True):
            if streaming:
                self.close_connection = True
            return
        self._touch()
        try:
            if p == "/api/upload":
                if n > MAX_UPLOAD:
                    self.close_connection = True
                    return self._send(413, {"error": "File too large (limit 2 GB)."})
                name = urllib.parse.unquote(self.headers.get("X-Filename") or "upload.bin")
                eid, safe, _ = STORE.save_upload(name, self.rfile, n)
                return self._send(200, {"id": eid, "name": safe})
            if p == "/api/logo":
                data = self.rfile.read(min(n, 5 * 1024 * 1024 + 1))
                if len(data) > 5 * 1024 * 1024:
                    return self._send(413, {"error": "Logo too large (limit 5 MB)."})
                ooxml.png_size(data)  # raises if not a PNG
                with open(STORE.logo_path(), "wb") as fh:
                    fh.write(data)
                return self._send(200, STORE.settings())
            body = self._json()
            if p == "/api/analyze":
                items = []
                for ev in body.get("evidence", [])[:50]:
                    if not isinstance(ev, dict) or not EID_RE.match(str(ev.get("id", ""))):
                        continue
                    path = STORE.evidence_path(ev["id"], ev.get("name", ""))
                    if os.path.isfile(path):
                        items.append((path, os.path.basename(path), ev["id"]))
                if not items:
                    return self._send(400, {"error": "No readable files were uploaded."})
                return self._send(200, analyze_paths(items))
            if p == "/api/scan":
                return self._send(200, scan_folder(body.get("path", "")))
            if p == "/api/usb":
                return self._send(200, usb_readout())
            if p == "/api/records":
                rid = STORE.save(body)
                return self._send(200, {"id": rid, "cert_code": STORE.get(rid)["cert_code"]})
            m = re.match(r"^/api/records/([0-9A-Za-z-]+)$", p)
            if m:
                rid = STORE.update(m.group(1), body)
                return self._send(200, STORE.get(rid))
            if p == "/api/verify":
                return self._send(200, STORE.verify(str(body.get("id", "")).strip(), body.get("code", "")))
            if p == "/api/settings":
                if body.get("signing_key"):
                    STORE.set_key(body.pop("signing_key"))
                return self._send(200, STORE.save_settings(body))
            if p == "/api/settings/reveal-key":
                return self._send(200, {"signing_key": STORE.key()})
            if p == "/api/ratings":
                match = str(body.get("match", ""))
                if not match or len(match) > 200:
                    return self._send(400, {"error": "Model match must be 1-200 characters."})
                re.compile(match)
                path = custom_ratings_path()
                data = {"models": []}
                if os.path.exists(path):
                    with open(path, encoding="utf-8") as fh:
                        data = json.load(fh)
                data["models"] = [x for x in data.get("models", []) if x.get("match") != match]
                if not body.get("delete"):
                    rated = int(body["rated"]) if str(body.get("rated") or "").strip() else None
                    if rated is not None and not 1000 <= rated <= 10_000_000:
                        return self._send(400, {"error": "Rated life must be between 1,000 and 10,000,000."})
                    data["models"].insert(0, {"brand": str(body.get("brand", ""))[:40],
                                              "name": str(body.get("name") or match)[:80], "match": match,
                                              "rated": rated, "electronic_only": bool(body.get("electronic_only")),
                                              "confidence": "store"})
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(data, fh, indent=1)
                return self._send(200, {"ok": True})
            if p == "/api/exiftool/recheck":
                exiftool.reset_cache()
                return self._send(200, status())
            self._send(404, {"error": "not found"})
        except (ValueError, KeyError, re.error) as ex:
            self._send(400, {"error": str(ex)})
        except Exception as ex:  # noqa: BLE001
            self._send(500, {"error": str(ex)})

    def do_DELETE(self):
        p = urllib.parse.urlparse(self.path).path
        self._body = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 65536))
        if not self._gate(p, write=True):
            return
        self._touch()
        m = re.match(r"^/api/records/([0-9A-Za-z-]+)$", p)
        if m and RID_RE.match(m.group(1)):
            STORE.delete(m.group(1))
            return self._send(200, {"ok": True})
        if p == "/api/logo":
            if os.path.isfile(STORE.logo_path()):
                os.remove(STORE.logo_path())
            return self._send(200, STORE.settings())
        self._send(404, {"error": "not found"})

    # -- files ----------------------------------------------------------------
    def _file(self, path, ctype=None):
        if not os.path.isfile(path):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ctype = ctype or mimetypes.guess_type(path)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8" if "charset" not in ctype else ""
        with open(path, "rb") as fh:
            self._send(200, fh.read(), ctype)

    def _evidence(self, eid, name):
        path = STORE.evidence_path(eid, name)
        if not os.path.isfile(path):
            return self._send(404, "not found", "text/plain; charset=utf-8")
        ext = os.path.splitext(path)[1].lower()
        inline = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
                  ".gif": "image/gif"}
        with open(path, "rb") as fh:
            data = fh.read()
        if ext in inline:
            return self._send(200, data, inline[ext], {"Content-Security-Policy": "default-src 'none'"})
        return self._download(data, os.path.basename(path), "application/octet-stream")


DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ---------------------------------------------------------------- launcher --

class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"   # on Windows this would let two servers share a port


def _existing_instance(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/hello", timeout=1.5) as r:
            return json.loads(r.read(2000)).get("app") == SIGNATURE
    except Exception:  # noqa: BLE001
        return False


def _bind(preferred):
    for port in (preferred, 0):
        try:
            return Server(("127.0.0.1", port), Handler)
        except OSError:
            continue
    raise OSError("Could not open a local port")


def open_window(url):
    """Open as a chromeless desktop window (Edge/Chrome app mode), else default browser."""
    cands = []
    if os.name == "nt":
        for base in [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]:
            if base:
                cands += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                          os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    elif sys.platform == "darwin":
        cands += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                  "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    else:
        cands += [shutil.which(n) or "" for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge")]
    profile = os.path.join(STORE.base if STORE else os.path.expanduser("~"), "window-profile")
    for c in cands:
        if c and os.path.isfile(c):
            try:
                subprocess.Popen([c, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1380,920",
                                  "--no-first-run", "--no-default-browser-check"])
                return
            except OSError:
                continue
    webbrowser.open(url)


def _idle_watchdog(limit_s):
    """Quit when the app window has been closed (no requests for a while)."""
    while True:
        time.sleep(30)
        if SEEN_CLIENT[0] and time.time() - LAST_SEEN[0] > limit_s:
            # grace period: after the PC wakes from sleep the window needs a moment to ping again
            time.sleep(90)
            if time.time() - LAST_SEEN[0] > limit_s:
                print("App window closed - shutting down.")
                os._exit(0)


def main(argv=None):
    global STORE
    argv = argv if argv is not None else sys.argv[1:]
    preferred = int(os.environ.get("CAMCHECK_PORT", "8765"))
    headless = "--no-window" in argv
    if not headless and _existing_instance(preferred):
        print("Camera Check is already running - opening its window.")
        open_window(f"http://127.0.0.1:{preferred}/")
        return
    STORE = Store()
    httpd = _bind(preferred)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"{__app_name__} {__version__} - {__publisher__}")
    print(f"Running at {url}")
    print(f"Data folder: {STORE.base}")
    cmd, ver = exiftool.find_exiftool()
    print(f"ExifTool: {ver or 'NOT FOUND - see README'}")
    if not headless:
        threading.Timer(0.6, open_window, args=(url,)).start()
        threading.Thread(target=_idle_watchdog, args=(600,), daemon=True).start()
    print("Close the app window (or this console) to quit.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
