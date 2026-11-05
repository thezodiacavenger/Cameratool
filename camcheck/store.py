"""Intake records (SQLite) and evidence files, kept in the user's data folder."""
import contextlib
import csv
import datetime as _dt
import io
import json
import os
import shutil
import sqlite3
import sys
import threading
import time
import uuid


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


FIELDS = ["id", "created_at", "staff", "reference", "seller", "brand", "model", "serial",
          "label_serial", "serial_match", "shutter_count", "count_source", "rated_life",
          "life_used_pct", "grade", "runtime_hours", "sensor_hours", "cosmetic",
          "functional", "offer", "notes", "flags", "analysis", "evidence"]


class Store:
    def __init__(self, base=None):
        self.base = base or data_dir()
        self.db_path = os.path.join(self.base, "records.sqlite")
        self.lock = threading.Lock()
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS records (
                id TEXT PRIMARY KEY, created_at TEXT, staff TEXT, reference TEXT, seller TEXT,
                brand TEXT, model TEXT, serial TEXT, label_serial TEXT, serial_match TEXT,
                shutter_count INTEGER, count_source TEXT, rated_life INTEGER, life_used_pct REAL,
                grade TEXT, runtime_hours REAL, sensor_hours REAL, cosmetic TEXT, functional TEXT,
                offer TEXT, notes TEXT, flags TEXT, analysis TEXT, evidence TEXT)""")
        self.cleanup_evidence()

    @contextlib.contextmanager
    def _conn(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()

    # -- evidence -----------------------------------------------------------
    def evidence_path(self, eid, name=None):
        d = os.path.join(self.base, "evidence", eid)
        if name is None:
            return d
        return os.path.join(d, name)

    def save_upload(self, name, stream, length):
        eid = uuid.uuid4().hex
        safe = os.path.basename(name).replace("\\", "_") or "upload.bin"
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
        import io as _io
        return self.save_upload(name, _io.BytesIO(data), len(data))

    def cleanup_evidence(self, max_age_h=24):
        """Delete uploads that never got attached to a saved record."""
        try:
            with self._conn() as c:
                used = set()
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

    # -- records ------------------------------------------------------------
    def save(self, rec):
        rec = dict(rec)
        rec.setdefault("id", _dt.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4])
        rec.setdefault("created_at", _dt.datetime.now().isoformat(sep=" ", timespec="seconds"))
        for k in ("flags", "analysis", "evidence"):
            if not isinstance(rec.get(k), str):
                rec[k] = json.dumps(rec.get(k) or [])
        row = {f: rec.get(f) for f in FIELDS}
        with self.lock, self._conn() as c:
            c.execute(f"INSERT OR REPLACE INTO records ({','.join(FIELDS)}) VALUES "
                      f"({','.join('?' * len(FIELDS))})", [row[f] for f in FIELDS])
        return row["id"]

    def get(self, rid):
        with self._conn() as c:
            r = c.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
        return self._decode(r) if r else None

    def list(self, q=""):
        sql = "SELECT * FROM records"
        args = []
        if q:
            sql += (" WHERE serial LIKE ? OR label_serial LIKE ? OR model LIKE ? OR reference LIKE ?"
                    " OR seller LIKE ? OR brand LIKE ?")
            args = [f"%{q}%"] * 6
        sql += " ORDER BY created_at DESC LIMIT 500"
        with self._conn() as c:
            return [self._decode(r, light=True) for r in c.execute(sql, args)]

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
        with self.lock, self._conn() as c:
            c.execute("DELETE FROM records WHERE id=?", (rid,))
        for ev in (rec or {}).get("evidence", []):
            shutil.rmtree(self.evidence_path(ev["id"]), ignore_errors=True)

    def _decode(self, r, light=False):
        d = dict(r)
        for k in ("flags", "evidence") + (() if light else ("analysis",)):
            try:
                d[k] = json.loads(d.get(k) or "[]")
            except ValueError:
                d[k] = []
        if light:
            d.pop("analysis", None)
        return d

    def export_csv(self):
        cols = [f for f in FIELDS if f not in ("analysis", "evidence")]
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        with self._conn() as c:
            for r in c.execute(f"SELECT {','.join(cols)} FROM records ORDER BY created_at"):
                row = list(r)
                fi = cols.index("flags")
                try:
                    row[fi] = " | ".join(f"{x['level']}: {x['msg']}" for x in json.loads(row[fi] or "[]"))
                except (ValueError, TypeError, KeyError):
                    pass
                w.writerow(row)
        return buf.getvalue()
