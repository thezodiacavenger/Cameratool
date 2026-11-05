"""Intake records (SQLite), evidence files, store settings and the certificate
signing key - all kept in the user's data folder."""
import base64
import contextlib
import csv
import datetime as _dt
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
import threading
import time
import uuid
import zipfile

SCHEMA_VERSION = 2
MAX_UPLOAD = 2 * 1024 ** 3          # 2 GB - large enough for cinema clips
EID_RE = re.compile(r"^[0-9a-f]{32}$")
RID_RE = re.compile(r"^[0-9A-Za-z-]{1,64}$")
STATUSES = ["Evaluated", "Purchased", "Declined", "Customer report", "Consignment", "Trade-in"]

DEFAULT_SETTINGS = {
    "store_name": "Vistek",
    "store_tagline": "Used Camera Condition Report",
    "store_address": "",
    "store_phone": "",
    "store_web": "vistek.ca",
    "report_disclaimer": (
        "Shutter life ratings are manufacturer or industry figures describing typical durability, "
        "not a guarantee. Actual shutter life varies with use, environment and maintenance. Counts "
        "are read from the camera's own records at the time of inspection."),
    "default_staff": "",
    "usage_profiles": [["Casual", 3000], ["Enthusiast", 10000], ["Working pro", 40000],
                       ["Sports / events", 100000]],
}


def data_dir():
    env = os.environ.get("CAMCHECK_DATA")
    if env:
        base = env
    elif os.name == "nt":
        base = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "CameraCheck")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support/CameraCheck")
    else:
        base = os.path.expanduser("~/.local/share/CameraCheck")
    os.makedirs(os.path.join(base, "evidence"), exist_ok=True)
    return base


def safe_filename(name):
    """Strip paths, control characters and leading dashes/dots from an uploaded name."""
    name = os.path.basename(str(name).replace("\\", "/"))
    name = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "_", name).strip().lstrip("-.")
    return name[:120] or "upload.bin"


FIELDS = ["id", "created_at", "updated_at", "status", "staff", "reference", "seller", "brand", "model",
          "serial", "label_serial", "serial_match", "shutter_count", "count_source", "rated_life",
          "life_used_pct", "grade", "runtime_hours", "sensor_hours", "firmware", "cosmetic",
          "functional", "offer", "notes", "cert_code", "flags", "analysis", "evidence", "extra"]
JSON_FIELDS = ("flags", "analysis", "evidence", "extra")
EDITABLE = {"status", "staff", "reference", "seller", "label_serial", "serial_match", "runtime_hours",
            "sensor_hours", "cosmetic", "functional", "offer", "notes", "extra", "brand", "model"}

MIGRATIONS = {
    1: ["""CREATE TABLE IF NOT EXISTS records (
            id TEXT PRIMARY KEY, created_at TEXT, staff TEXT, reference TEXT, seller TEXT,
            brand TEXT, model TEXT, serial TEXT, label_serial TEXT, serial_match TEXT,
            shutter_count INTEGER, count_source TEXT, rated_life INTEGER, life_used_pct REAL,
            grade TEXT, runtime_hours REAL, sensor_hours REAL, cosmetic TEXT, functional TEXT,
            offer TEXT, notes TEXT, flags TEXT, analysis TEXT, evidence TEXT)"""],
    2: ["ALTER TABLE records ADD COLUMN updated_at TEXT",
        "ALTER TABLE records ADD COLUMN status TEXT",
        "ALTER TABLE records ADD COLUMN firmware TEXT",
        "ALTER TABLE records ADD COLUMN cert_code TEXT",
        "ALTER TABLE records ADD COLUMN extra TEXT",
        "CREATE INDEX IF NOT EXISTS idx_records_serial ON records(serial)",
        "CREATE INDEX IF NOT EXISTS idx_records_created ON records(created_at)"],
}


def csv_safe(v):
    """Neutralise spreadsheet formula injection (=, +, -, @, tab, CR at the start of a cell)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


class Store:
    def __init__(self, base=None):
        self.base = base or data_dir()
        os.makedirs(os.path.join(self.base, "evidence"), exist_ok=True)
        self.db_path = os.path.join(self.base, "records.sqlite")
        self.lock = threading.Lock()
        self.migrate()
        self.cleanup_evidence()

    @contextlib.contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def migrate(self):
        with self.lock, self._conn() as c:
            ver = c.execute("PRAGMA user_version").fetchone()[0]
            exists = c.execute("SELECT name FROM sqlite_master WHERE name='records'").fetchone()
            if ver == 0 and exists:
                ver = 1  # databases from v1.x had no version stamp
            cols = {r[1] for r in c.execute("PRAGMA table_info(records)")} if exists else set()
            for v in range(ver + 1, SCHEMA_VERSION + 1):
                for sql in MIGRATIONS[v]:
                    m = re.match(r"ALTER TABLE records ADD COLUMN (\w+)", sql)
                    if m and m.group(1) in cols:
                        continue
                    c.execute(sql)
                c.execute(f"PRAGMA user_version = {v}")

    # -- settings / signing key -----------------------------------------------
    def settings(self):
        path = os.path.join(self.base, "settings.json")
        s = dict(DEFAULT_SETTINGS)
        try:
            with open(path, encoding="utf-8") as fh:
                s.update({k: v for k, v in json.load(fh).items() if k in DEFAULT_SETTINGS})
        except (OSError, ValueError):
            pass
        s["has_logo"] = os.path.isfile(self.logo_path())
        return s

    def save_settings(self, upd):
        s = self.settings()
        s.pop("has_logo", None)
        for k, v in upd.items():
            if k not in DEFAULT_SETTINGS:
                continue
            if k == "usage_profiles":
                v = [[str(n)[:40], int(x)] for n, x in v if int(x) > 0][:8]
            elif isinstance(v, str):
                v = v[:2000]
            s[k] = v
        with open(os.path.join(self.base, "settings.json"), "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=1)
        return self.settings()

    def logo_path(self):
        return os.path.join(self.base, "logo.png")

    def key(self):
        path = os.path.join(self.base, "signing.key")
        try:
            with open(path, encoding="ascii") as fh:
                k = fh.read().strip()
            if len(k) >= 32:
                return k
        except OSError:
            pass
        k = secrets.token_urlsafe(32)
        with open(path, "w", encoding="ascii") as fh:
            fh.write(k)
        return k

    def set_key(self, k):
        k = str(k).strip()
        if not re.match(r"^[A-Za-z0-9_-]{32,128}$", k):
            raise ValueError("Signing key must be 32-128 letters, digits, '-' or '_'.")
        with open(os.path.join(self.base, "signing.key"), "w", encoding="ascii") as fh:
            fh.write(k)

    @staticmethod
    def _cert_payload(rec):
        keys = ("id", "created_at", "brand", "model", "serial", "shutter_count", "rated_life",
                "life_used_pct", "grade", "runtime_hours", "sensor_hours")
        return json.dumps({k: ("" if rec.get(k) is None else str(rec.get(k))) for k in keys},
                          sort_keys=True).encode("utf-8")

    def cert_code(self, rec):
        mac = hmac.new(self.key().encode(), self._cert_payload(rec), hashlib.sha256).digest()
        code = base64.b32encode(mac).decode()[:12]
        return f"{code[:4]}-{code[4:8]}-{code[8:12]}"

    def verify(self, rid, code):
        rec = self.get(rid)
        if not rec:
            return {"valid": False, "reason": "No record with that certificate ID exists on this computer."}
        want = self.cert_code(rec)
        given = re.sub(r"[^A-Z2-7]", "", str(code).upper())
        ok = hmac.compare_digest(want.replace("-", ""), given)
        return {"valid": ok, "record": {k: rec.get(k) for k in (
            "id", "created_at", "brand", "model", "serial", "shutter_count", "life_used_pct",
            "grade", "runtime_hours", "status", "extra")},
            "reason": None if ok else "Code does not match - the report may have been altered, or it was "
                                      "issued by a computer with a different signing key."}

    # -- evidence -------------------------------------------------------------
    def evidence_path(self, eid, name=None):
        if not EID_RE.match(str(eid)):
            raise ValueError("invalid evidence id")
        d = os.path.join(self.base, "evidence", eid)
        if name is None:
            return d
        return os.path.join(d, safe_filename(name))

    def save_upload(self, name, stream, length):
        if length > MAX_UPLOAD:
            raise ValueError("File too large (limit 2 GB).")
        eid = uuid.uuid4().hex
        safe = safe_filename(name)
        d = self.evidence_path(eid)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, safe)
        remaining = length
        with open(path, "wb") as fh:
            while remaining > 0:
                chunk = stream.read(min(1 << 20, remaining))
                if not chunk:
                    break
                fh.write(chunk)
                remaining -= len(chunk)
        return eid, safe, path

    def save_bytes(self, name, data):
        return self.save_upload(name, io.BytesIO(data), len(data))

    def cleanup_evidence(self, max_age_h=24):
        """Delete uploads that never got attached to a saved record."""
        try:
            used = set()
            with self._conn() as c:
                for (ev,) in c.execute("SELECT evidence FROM records"):
                    for item in json.loads(ev or "[]"):
                        used.add(item.get("id"))
            root = os.path.join(self.base, "evidence")
            for eid in os.listdir(root):
                p = os.path.join(root, eid)
                if eid not in used and time.time() - os.path.getmtime(p) > max_age_h * 3600:
                    shutil.rmtree(p, ignore_errors=True)
        except (OSError, ValueError, sqlite3.Error):
            pass

    # -- records --------------------------------------------------------------
    @staticmethod
    def _clean(rec):
        rec = dict(rec)
        for k in ("shutter_count", "rated_life"):
            try:
                rec[k] = int(rec[k]) if rec.get(k) not in (None, "") else None
            except (TypeError, ValueError):
                rec[k] = None
        for k in ("life_used_pct", "runtime_hours", "sensor_hours"):
            try:
                rec[k] = float(rec[k]) if rec.get(k) not in (None, "") else None
            except (TypeError, ValueError):
                rec[k] = None
        if rec.get("status") not in STATUSES:
            rec["status"] = "Evaluated"
        for k, v in list(rec.items()):
            if isinstance(v, str):
                rec[k] = v[:5000]
        ev = []
        for item in rec.get("evidence") or []:
            if isinstance(item, dict) and EID_RE.match(str(item.get("id", ""))):
                ev.append({"id": item["id"], "name": safe_filename(item.get("name", ""))})
        rec["evidence"] = ev
        return rec

    def save(self, rec):
        rec = self._clean(rec)
        now = _dt.datetime.now().isoformat(sep=" ", timespec="seconds")
        if not rec.get("id") or not RID_RE.match(str(rec["id"])):
            rec["id"] = _dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4].upper()
        rec.setdefault("created_at", now)
        rec["created_at"] = rec.get("created_at") or now
        rec["updated_at"] = now
        rec["cert_code"] = self.cert_code(rec)
        for k in JSON_FIELDS:
            if not isinstance(rec.get(k), str):
                rec[k] = json.dumps(rec.get(k) or ([] if k != "extra" else {}))
        row = {f: rec.get(f) for f in FIELDS}
        with self.lock, self._conn() as c:
            c.execute(f"INSERT OR REPLACE INTO records ({','.join(FIELDS)}) VALUES "
                      f"({','.join('?' * len(FIELDS))})", [row[f] for f in FIELDS])
        return row["id"]

    def update(self, rid, changes):
        rec = self.get(rid)
        if not rec:
            raise KeyError(rid)
        for k, v in changes.items():
            if k in EDITABLE:
                rec[k] = v
        return self.save(rec)

    def get(self, rid):
        if not RID_RE.match(str(rid)):
            return None
        with self._conn() as c:
            r = c.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        return self._decode(r) if r else None

    def list(self, q="", status=""):
        sql = "SELECT * FROM records WHERE 1=1"
        args = []
        if q:
            sql += (" AND (serial LIKE ? OR label_serial LIKE ? OR model LIKE ? OR reference LIKE ?"
                    " OR seller LIKE ? OR brand LIKE ? OR id LIKE ? OR staff LIKE ?)")
            args += [f"%{q}%"] * 8
        if status:
            sql += " AND status = ?"
            args.append(status)
        sql += " ORDER BY created_at DESC LIMIT 1000"
        with self._conn() as c:
            return [self._decode(r, light=True) for r in c.execute(sql, args)]

    def all(self):
        with self._conn() as c:
            return [self._decode(r, light=True) for r in c.execute("SELECT * FROM records ORDER BY created_at")]

    def stats(self):
        with self._conn() as c:
            row = c.execute("""SELECT COUNT(*) n, AVG(life_used_pct) avg_pct,
                SUM(CASE WHEN serial_match='MISMATCH' THEN 1 ELSE 0 END) mismatches,
                SUM(CASE WHEN status='Purchased' THEN 1 ELSE 0 END) purchased,
                SUM(CASE WHEN created_at >= date('now','-30 day') THEN 1 ELSE 0 END) last30
                FROM records""").fetchone()
        return dict(row)

    def history_for_serial(self, serial, exclude=None):
        if not serial:
            return []
        with self._conn() as c:
            rows = c.execute("SELECT id, created_at, shutter_count, runtime_hours, grade FROM records "
                             "WHERE (serial=? OR label_serial=?) AND id<>? ORDER BY created_at",
                             (serial, serial, exclude or "")).fetchall()
        return [dict(r) for r in rows]

    def delete(self, rid):
        rec = self.get(rid)
        if not rec:
            return
        with self.lock, self._conn() as c:
            c.execute("DELETE FROM records WHERE id=?", (rid,))
        for ev in rec.get("evidence", []):
            with contextlib.suppress(ValueError):
                shutil.rmtree(self.evidence_path(ev["id"]), ignore_errors=True)

    def _decode(self, r, light=False):
        d = dict(r)
        for k in ("flags", "evidence", "extra") + (() if light else ("analysis",)):
            try:
                d[k] = json.loads(d.get(k) or ("{}" if k == "extra" else "[]"))
            except ValueError:
                d[k] = {} if k == "extra" else []
        if light:
            d.pop("analysis", None)
        d["status"] = d.get("status") or "Evaluated"
        return d

    # -- exports --------------------------------------------------------------
    EXPORT_COLS = [("id", "Record / certificate ID"), ("created_at", "Date"), ("status", "Status"),
                   ("camera", "Camera"), ("staff", "Staff"), ("reference", "PO / trade-in #"), ("seller", "Seller / customer"),
                   ("brand", "Brand"), ("model", "Model"), ("serial", "Serial (camera)"),
                   ("label_serial", "Serial (label)"), ("serial_match", "Serial match"),
                   ("shutter_count", "Shutter count"), ("rated_life", "Rated life"),
                   ("life_used_pct", "Life used %"), ("grade", "Shutter grade"),
                   ("runtime_hours", "Runtime h"), ("sensor_hours", "Sensor h"), ("firmware", "Firmware"),
                   ("cosmetic", "Cosmetic"), ("functional", "Functional checks"), ("offer", "Offer"),
                   ("count_source", "Count source"), ("cert_code", "Verification code"),
                   ("flags", "Flags"), ("notes", "Notes")]

    def export_rows(self, records=None):
        rows = []
        for r in records if records is not None else self.all():
            row = []
            for k, _ in self.EXPORT_COLS:
                v = r.get(k)
                if k == "camera":
                    from .reportcard import _name
                    v = _name(r)
                elif k == "flags":
                    v = " | ".join(f"{x.get('level', '')}: {x.get('msg', '')}" for x in v or [])
                row.append(v)
            rows.append(row)
        return [label for _, label in self.EXPORT_COLS], rows

    def export_csv(self):
        head, rows = self.export_rows()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(head)
        for row in rows:
            w.writerow([csv_safe(v) for v in row])
        return "﻿" + buf.getvalue()  # BOM so Excel opens UTF-8 correctly

    def backup_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            snap = os.path.join(self.base, "backup-snapshot.sqlite")
            with self._conn() as c, contextlib.closing(sqlite3.connect(snap)) as dst:
                c.backup(dst)
            z.write(snap, "records.sqlite")
            os.remove(snap)
            for name in ("settings.json", "ratings_custom.json", "logo.png"):
                p = os.path.join(self.base, name)
                if os.path.isfile(p):
                    z.write(p, name)
            root = os.path.join(self.base, "evidence")
            used = set()
            for r in self.all():
                used.update(e["id"] for e in r.get("evidence") or [])
            for eid in used:
                d = os.path.join(root, eid)
                if os.path.isdir(d):
                    for n in os.listdir(d):
                        z.write(os.path.join(d, n), f"evidence/{eid}/{n}")
            z.writestr("README.txt", "Camera Check backup. To restore: close Camera Check and unzip "
                                     "into the data folder shown under Settings (signing.key is not "
                                     "included - copy it separately if you need old certificates to verify).")
        return buf.getvalue()
