"""Direct USB camera readout.

Windows : native PTP over Windows Portable Devices (camcheck.ptp.wpd) - stock drivers.
macOS/Linux : gphoto2 command-line tool.

Both paths do two things:
  1. read shutter counters the body exposes over PTP (Canon EOS, Fujifilm, Olympus/OM), and
  2. download the newest photo from the camera's card so its MakerNotes can be decoded
     (this is how Nikon, Sony, Pentax and Canon mirrorless counts are read over USB).
"""
import os
import re
import shutil
import subprocess
import sys
import threading

from .ptp import core

CAMERA_MAKERS = re.compile(r"nikon|canon|sony|fuji|olympus|om digital|panasonic|leica|hasselblad|"
                           r"pentax|ricoh|sigma|blackmagic|red|arri|z ?cam|kinefinity|dji|phase", re.I)
MAX_RAW_BYTES = 24 * 1024 * 1024   # MakerNotes live near the start of RAW files
MAX_FULL_BYTES = 80 * 1024 * 1024
_lock = threading.Lock()


def _no_window():
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


# ------------------------------------------------------------------ status --

def gphoto2_path():
    for p in (shutil.which("gphoto2"), "/opt/homebrew/bin/gphoto2", "/usr/local/bin/gphoto2"):
        if p and os.path.isfile(p):
            return p
    return None


def backend():
    if os.name == "nt":
        from .ptp import wpd
        ok, err = wpd.available()
        return {"name": "Windows WPD (native)", "ok": ok,
                "error": None if ok else f"comtypes not available ({err}). Re-run the Windows launcher "
                                         "or: py -3 -m pip install comtypes"}
    g = gphoto2_path()
    return {"name": "gphoto2", "ok": bool(g), "path": g,
            "error": None if g else "Install gphoto2 (macOS: brew install gphoto2 / Linux: apt install gphoto2)"}


# ----------------------------------------------------------------- windows --

def _read_wpd(save_bytes, want_image=True):
    from .ptp import wpd
    comtypes = wpd._load()[0]
    inited = False
    try:
        comtypes.CoInitializeEx(0)  # COINIT_MULTITHREADED; this request runs on its own thread
        inited = True
    except OSError:
        pass  # already initialised on this thread
    try:
        devices = wpd.list_devices()
        result = {"ok": False, "transport": "Windows WPD", "devices": [n for _, n in devices], "log": []}
        if not devices:
            result["error"] = ("No USB camera found. Connect the camera with a data cable, switch it on and "
                               "set its USB mode to PTP / MTP / 'PC Remote' / 'Tether'. Cameras set to "
                               "'Mass Storage' appear as a card drive instead - use the Card readers panel.")
            return result
        tried = []
        # cameras first
        devices.sort(key=lambda d: 0 if CAMERA_MAKERS.search(d[1]) else 1)
        for pnp, name in devices:
            t = None
            try:
                t = wpd.WPDTransport(pnp)
                s = core.Session(t)
                di = s.device_info()
                if not CAMERA_MAKERS.search(di["manufacturer"] + " " + di["model"]) and len(devices) > 1:
                    tried.append(f"{name}: {di['manufacturer']} {di['model']} (not a camera)")
                    continue
                return _session_readout(s, result, save_bytes, want_image)
            except Exception as ex:  # noqa: BLE001
                tried.append(f"{name}: {ex}")
            finally:
                if t:
                    t.close()
        result["error"] = "Could not talk to the connected device(s): " + "; ".join(tried)
        return result
    finally:
        if inited:
            try:
                comtypes.CoUninitialize()
            except Exception:  # noqa: BLE001
                pass


def _session_readout(s, result, save_bytes, want_image):
    di = s.info
    result.update({"ok": True, "manufacturer": di["manufacturer"], "model": di["model"],
                   "firmware": di["device_version"], "serial": di["serial"] or None,
                   "vendor_ext": di["vendor_ext_desc"]})
    counters = s.vendor_counters()
    errors = counters.pop("_errors", [])
    if counters.get("_serial") and not result["serial"]:
        result["serial"] = counters["_serial"]
    counters.pop("_serial", None)
    result["counters"] = counters
    result["shutter_count"] = next(iter(counters.values()), None)
    if want_image:
        try:
            imgs = s.newest_images()
            pick = s.pick_download(imgs)
            if pick:
                is_jpeg = pick["filename"].lower().endswith(core.JPEG_EXT)
                limit = MAX_FULL_BYTES if is_jpeg else MAX_RAW_BYTES
                data, partial = s.get_object(pick["handle"], max_bytes=limit, size=pick["size"])
                eid, name, path = save_bytes(pick["filename"], data)
                result["image"] = {"name": name, "evidence_id": eid, "path": path,
                                   "capture_date": pick["capture_date"], "partial": partial,
                                   "bytes": len(data), "recent": [o["filename"] for o in imgs[:5]]}
            else:
                result["image_note"] = "No photos on the camera's card. Take a frame and read again."
        except Exception as ex:  # noqa: BLE001
            errors.append(f"image download: {ex}")
    result["errors"] = errors
    result["log"] = s.log[-40:]
    return result


# ------------------------------------------------------------ mac / linux --

def _g(args, timeout=40):
    out = subprocess.run([gphoto2_path()] + args, capture_output=True, text=True, timeout=timeout)
    return out.returncode, out.stdout, out.stderr


def _read_gphoto2(save_bytes, want_image=True):
    if sys.platform == "darwin":
        for proc in ("PTPCamera", "ptpcamerad"):
            subprocess.run(["killall", proc], capture_output=True)
    rc, out, err = _g(["--auto-detect"])
    cams = [l.strip() for l in out.splitlines()[2:] if l.strip()]
    result = {"ok": False, "transport": "gphoto2", "devices": cams, "log": []}
    if not cams:
        result["error"] = ("No camera detected. Connect by USB, switch the camera on and set USB mode to "
                           "PTP/MTP or 'PC Remote'. Close Photos/Image Capture.")
        return result
    result["ok"] = True
    rc, out, err = _g(["--summary"])
    summ = dict(re.findall(r"^\s*(Manufacturer|Model|Version|Serial Number)\s*:\s*(.+)$", out, re.M))
    result.update({"manufacturer": summ.get("Manufacturer"), "model": summ.get("Model"),
                   "firmware": summ.get("Version"), "serial": summ.get("Serial Number")})
    counters = {}
    rc, out, err = _g(["--list-config"])
    keys = [k.strip() for k in out.splitlines()
            if k.strip().startswith("/") and re.search(r"shuttercount|shuttercounter|serialnumber", k, re.I)]
    for key in keys[:6]:
        rc, val, _ = _g(["--get-config", key])
        m = re.search(r"^Current:\s*(.*)$", val, re.M)
        if not m:
            continue
        if "serial" in key.lower():
            result["serial"] = result.get("serial") or m.group(1).strip()
        else:
            digits = re.sub(r"\D", "", m.group(1))
            if digits:
                counters[f"Shutter counter ({key})"] = int(digits)
    result["counters"] = counters
    result["shutter_count"] = next(iter(counters.values()), None)
    result["errors"] = []
    if want_image:
        rc, out, err = _g(["--list-files"], timeout=120)
        files = re.findall(r"^#(\d+)\s+(\S+)", out, re.M)
        imgs = [(int(n), f) for n, f in files if f.lower().endswith(core.IMAGE_EXT)]
        if imgs:
            jpgs = [x for x in imgs[-4:] if x[1].lower().endswith(core.JPEG_EXT)]
            num, fname = (jpgs or imgs)[-1]
            import tempfile
            tmp = os.path.join(tempfile.mkdtemp(), fname)
            rc, out, err = _g(["--get-file", str(num), "--filename", tmp, "--force-overwrite"], timeout=300)
            if os.path.isfile(tmp):
                with open(tmp, "rb") as fh:
                    eid, name, path = save_bytes(fname, fh.read())
                shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)
                result["image"] = {"name": name, "evidence_id": eid, "path": path, "partial": False}
            else:
                result["errors"].append(f"download failed: {err.strip()[:200]}")
        else:
            result["image_note"] = "No photos on the camera's card. Take a frame and read again."
    return result


# -------------------------------------------------------------------- api --

def read_camera(save_bytes, want_image=True):
    """save_bytes(name, data) -> (evidence_id, safe_name, path)."""
    b = backend()
    if not b["ok"]:
        return {"ok": False, "error": b["error"]}
    with _lock:
        if os.name == "nt":
            return _read_wpd(save_bytes, want_image)
        return _read_gphoto2(save_bytes, want_image)
