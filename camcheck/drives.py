"""Detect memory cards in card readers (SD, microSD, CFexpress Type A/B, CFast, XQD...)
and find the camera files on them.

CFexpress readers are NVMe-over-USB and Windows often reports them as *fixed* disks,
not removable - so every non-system volume is checked for a camera folder layout.
"""
import ctypes
import glob
import os
import re
import string
import sys
import xml.etree.ElementTree as ET

# folder signatures -> what kind of camera wrote the card
LAYOUTS = [
    ("DCIM", "Stills camera (DCIM)"),
    ("PRIVATE/M4ROOT", "Sony XAVC / cinema (M4ROOT)"),
    ("PRIVATE/XDROOT", "Sony XDCAM"),
    ("XDROOT", "Sony XDCAM"),
    ("PRIVATE/AVCHD", "AVCHD video"),
    ("CONTENTS/CLIPS001", "Canon Cinema EOS (XF/MP4)"),
    ("CONTENTS", "Canon / Panasonic pro video"),
    ("PRIVATE/PANA_GRP", "Panasonic pro video"),
    ("CLIP", "Video clips"),
]
CLIP_EXT = re.compile(r"\.(r3d|mxf|mov|mp4|braw|crm|ari|arx|mts)$", re.I)
PHOTO_EXT = re.compile(r"\.(jpe?g|nef|nrw|cr2|cr3|crw|arw|srf|sr2|raf|orf|ori|rw2|rwl|pef|dng|"
                       r"3fr|fff|iiq|x3f|srw|hif|heic|heif)$", re.I)


def _win_volumes():
    k32 = ctypes.windll.kernel32
    sysdrive = (os.environ.get("SystemDrive") or "C:").upper().rstrip("\\")
    mask = k32.GetLogicalDrives()
    out = []
    for i, letter in enumerate(string.ascii_uppercase):
        if not mask & (1 << i):
            continue
        root = f"{letter}:\\"
        dtype = k32.GetDriveTypeW(root)  # 2 removable, 3 fixed, 4 network, 5 cdrom
        if dtype not in (2, 3) or f"{letter}:" == sysdrive:
            continue
        label = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        old = k32.SetErrorMode(1)  # no "insert disk" popups for empty readers
        try:
            if not k32.GetVolumeInformationW(root, label, 261, None, None, None, fs, 261):
                continue  # empty card slot
        finally:
            k32.SetErrorMode(old)
        total = ctypes.c_ulonglong(0)
        free = ctypes.c_ulonglong(0)
        k32.GetDiskFreeSpaceExW(root, None, ctypes.byref(total), ctypes.byref(free))
        out.append({"path": root, "label": label.value, "fs": fs.value, "removable": dtype == 2,
                    "size": total.value, "free": free.value})
    return out


def _posix_volumes():
    roots = []
    if sys.platform == "darwin":
        roots = [p for p in glob.glob("/Volumes/*") if os.path.realpath(p) != "/"]
    else:
        user = os.environ.get("USER", "")
        roots = glob.glob(f"/media/{user}/*") + glob.glob("/run/media/*/*") + glob.glob("/media/*") + glob.glob("/mnt/*")
    out = []
    seen = set()
    for r in roots:
        if not os.path.isdir(r) or r in seen:
            continue
        seen.add(r)
        try:
            st = os.statvfs(r)
            size, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        except OSError:
            size = free = 0
        out.append({"path": r, "label": os.path.basename(r), "fs": "", "removable": True,
                    "size": size, "free": free})
    return out


def detect_layout(root):
    found = []
    try:
        top = {n.upper(): n for n in os.listdir(root)}
    except OSError:
        return found
    for sig, desc in LAYOUTS:
        parts = sig.split("/")
        if parts[0] in top:
            p = os.path.join(root, top[parts[0]], *parts[1:])
            if os.path.isdir(p):
                found.append(desc)
    if any(n.endswith(".RDM") for n in top):
        found.append("RED (RDM/RDC)")
    if any(CLIP_EXT.search(n) for n in top):
        found.append("Clips in card root (ARRI / Blackmagic / other)")
    return list(dict.fromkeys(found))


def list_cards(volumes=None):
    if volumes is None:
        vols = _win_volumes() if os.name == "nt" else _posix_volumes()
        # extra locations to watch (path-separator list), e.g. CAMCHECK_CARD_ROOTS=X:/;Y:/
        for extra in filter(None, os.environ.get("CAMCHECK_CARD_ROOTS", "").split(os.pathsep)):
            if os.path.isdir(extra):
                vols.append({"path": extra, "label": os.path.basename(extra.rstrip("\\/")) or extra,
                             "fs": "", "removable": True, "size": 0, "free": 0})
    else:
        vols = volumes
    cards = []
    for v in vols:
        v["layouts"] = detect_layout(v["path"])
        # fixed disks without camera folders are ordinary drives, not cards
        if v["layouts"] or v["removable"]:
            v["camera_card"] = bool(v["layouts"])
            cards.append(v)
    cards.sort(key=lambda c: not c["camera_card"])
    return cards


def newest_files(root, limit=24, max_walk=30000):
    photos, clips, xml = [], [], []
    n = 0
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.upper() not in ("$RECYCLE.BIN", "SYSTEM VOLUME INFORMATION")]
        for name in names:
            n += 1
            if name.startswith("._") or "\n" in name or "\r" in name:
                continue
            p = os.path.join(dirpath, name)
            try:
                m = os.path.getmtime(p)
            except OSError:
                continue
            if PHOTO_EXT.search(name):
                photos.append((m, p))
            elif CLIP_EXT.search(name):
                clips.append((m, p))
            elif name.upper().endswith(".XML") and re.search(r"M\d\d\.XML$|\.XML$", name.upper()):
                xml.append((m, p))
        if n > max_walk:
            break
    photos.sort(reverse=True)
    clips.sort(reverse=True)
    xml.sort(reverse=True)
    return [p for _, p in photos[:limit]], [p for _, p in clips[:6]], [p for _, p in xml[:6]]


def sony_nrt(path):
    """Parse a Sony NonRealTimeMeta clip sidecar (…M01.XML) for model + serial."""
    try:
        tree = ET.parse(path)
    except (ET.ParseError, OSError):
        return None
    for el in tree.iter():
        if el.tag.split("}")[-1] == "Device":
            a = el.attrib
            if a.get("serialNo") or a.get("modelName"):
                return {"file": os.path.basename(path), "make": a.get("manufacturer"),
                        "model": a.get("modelName"), "serial": a.get("serialNo")}
    return None
