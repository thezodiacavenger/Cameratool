"""Turn raw ExifTool output into a buy-side camera condition assessment."""
import datetime as _dt
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")

VIDEO_TYPES = {"R3D", "MXF", "MOV", "MP4", "M4V", "BRAW", "CRM", "MTS", "M2TS", "AVI", "XML"}
NA_VALUES = {4294965247, 4294967295, 65535 * 65537}

EDITING_SOFTWARE = re.compile(
    r"photoshop|lightroom|capture one|gimp|luminar|affinity|dxo|snapseed|darktable|"
    r"rawtherapee|pixelmator|acdsee|on1|picasa|windows photo|photos \d|exiftool|"
    r"capture nx|digital photo professional|imaging edge|silkypix|x raw studio|"
    r"nx studio|olympus workspace|om workspace", re.I)

MAKER_GROUPS = {"Nikon", "Canon", "Sony", "FujiFilm", "Olympus", "Panasonic", "Pentax",
                "Leica", "Ricoh", "Sigma", "Samsung", "Minolta", "Casio", "Kodak",
                "Hasselblad", "PhaseOne", "Red", "Apple", "DJI", "GoPro", "MakerNotes",
                "NikonCustom", "CanonCustom", "SonyIDC", "Leica2", "Leica5"}

# ---------------------------------------------------------------- brand ----

_BRANDS = [
    (r"nikon", "Nikon"), (r"canon", "Canon"), (r"sony", "Sony"),
    (r"fuji", "Fujifilm"), (r"olympus|om digital|om system", "OM System"),
    (r"panasonic", "Panasonic"), (r"leica", "Leica"), (r"hasselblad", "Hasselblad"),
    (r"pentax|ricoh", "Pentax"), (r"phase ?one", "Phase One"), (r"\bred\b|red digital", "RED"),
    (r"arri", "ARRI"), (r"blackmagic", "Blackmagic Design"), (r"sigma", "Sigma"),
    (r"dji", "DJI"), (r"gopro", "GoPro"), (r"z ?cam", "Z CAM"), (r"kinefinity", "Kinefinity"),
]


_CINEMA = [
    (r"sony", r"^(ILME-|MPC-|PXW-|PMW-|FX[369]|FR7|VENICE|BURANO|HDC-)", "Sony Cinema"),
    (r"canon", r"EOS C\d|^C\d{2,3}", "Canon Cinema EOS"),
    (r"panasonic", r"^(AU-|AG-|VARICAM|EVA)", "Panasonic Cinema"),
]


_MAKE_PREFIX = re.compile(r"^(nikon( corporation)?|canon|sony|fujifilm|fuji|olympus|om digital solutions|om system|"
                          r"panasonic|leica|hasselblad|pentax|ricoh|sigma|red|arri|blackmagic( design)?)\s+", re.I)


def display_name(brand, model, rating=None):
    """Human name: 'Nikon Z 6II', 'Sony Alpha 7 III', 'Canon EOS R5' (never 'Canon Canon EOS R5')."""
    model = (model or "").strip()
    if rating and rating.get("name") and "/" not in rating["name"]:
        return f"{brand} {rating['name']}".strip()
    rest = _MAKE_PREFIX.sub("", model).strip() or model
    if brand and rest.lower().startswith(brand.lower()):
        return rest
    return f"{brand} {rest}".strip() if brand and brand != "Unknown" else rest


pretty_name = display_name  # analyze() has a parameter called display_name


def normalize_brand(make, model=""):
    for mk, md, name in _CINEMA:
        if re.search(mk, f"{make or ''} {model or ''}", re.I) and re.search(md, (model or "").strip(), re.I):
            return name
    s = f"{make or ''} {model or ''}".strip()
    for pat, name in _BRANDS:
        if re.search(pat, s, re.I):
            return name
    return (make or "Unknown").strip() or "Unknown"


# ---------------------------------------------------------- tag access -----

class Tags:
    """Case-insensitive access to 'Group:Tag' keys, ignoring group and duplicates."""

    def __init__(self, raw):
        self.raw = raw
        self.by_name = {}
        for key, val in raw.items():
            if key in ("SourceFile",):
                continue
            base = key.split(" #")[0]
            group, _, name = base.rpartition(":")
            self.by_name.setdefault(name.lower(), []).append((group, val, key))

    def all(self, name):
        return self.by_name.get(name.lower(), [])

    def first(self, *names, groups=None):
        for n in names:
            for g, v, _ in self.all(n):
                if groups and g not in groups:
                    continue
                if v not in (None, "", "n/a"):
                    return v
        return None

    def groups(self):
        return {g for items in self.by_name.values() for g, _, _ in items}

    def search(self, pattern):
        rx = re.compile(pattern, re.I)
        out = []
        for name, items in self.by_name.items():
            for g, v, key in items:
                if rx.search(name):
                    out.append((key, v))
        return out


def _as_int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        iv = int(v)
    else:
        m = re.match(r"^\s*(\d+)\s*$", str(v))
        if not m:
            return None
        iv = int(m.group(1))
    if iv in NA_VALUES or iv < 0:
        return None
    return iv


def _parse_dt(v):
    if not v or not isinstance(v, str):
        return None
    m = re.match(r"(\d{4}):(\d\d):(\d\d)[ T](\d\d):(\d\d):(\d\d)", v)
    if not m:
        return None
    try:
        d = _dt.datetime(*map(int, m.groups()))
    except ValueError:
        return None
    return None if d.year < 1990 else d


# ------------------------------------------------------------ counters -----

def _counter(tags, name, label, kind, reliability, note="", groups=None):
    vals = []
    for g, v, key in tags.all(name):
        if groups and g not in groups:
            continue
        iv = _as_int(v)
        if iv is not None and iv > 0 and (key, iv) not in vals:
            vals.append((key, iv))
    if not vals:
        return []
    # identical values from several sub-tables -> one entry
    uniq = {}
    for key, iv in vals:
        uniq.setdefault(iv, key)
    return [{"label": label, "value": iv, "tag": key, "kind": kind,
             "reliability": reliability, "note": note} for iv, key in uniq.items()]


def extract_counters(brand, model, tags, file_type):
    c = []
    if brand == "Nikon":
        c += _counter(tags, "MechanicalShutterCount", "Mechanical shutter count", "mechanical", "high",
                      "Mechanical curtain actuations only.")
        c += _counter(tags, "ShutterCount", "Total releases (ShutterCount)", "total", "high",
                      "All releases including electronic/silent shutter on mirrorless bodies.")
    elif brand == "Canon":
        rel = "low" if re.search(r"1D.?s? ?Mark III", model or "", re.I) else "high"
        c += _counter(tags, "ShutterCount", "Shutter count (mech + electronic)", "total", rel,
                      "Canon includes electronic-shutter frames in this figure.")
        c += _counter(tags, "ImageCount", "Image count (resets on card format)", "image", "low",
                      "Not a lifetime counter - informational only.")
    elif brand == "Sony":
        wrap = bool(re.match(r"^ILCE-7R?$|^ILCE-7S$", model or ""))
        c += _counter(tags, "ShutterCount", "Shutter count (mechanical releases)", "mechanical", "high",
                      "Does not advance in silent shooting." +
                      (" This model wraps at 65,536 - the true figure may be higher by multiples of 65,536." if wrap else ""))
        c += _counter(tags, "ShutterCount2", "Shutter count 2", "mechanical", "medium",
                      "Secondary counter (some A7/A6000-era models).")
        c += _counter(tags, "ShutterCount3", "Shutter count 3", "mechanical", "medium",
                      "Secondary counter (SLT/NEX/early ILCE models).")
        c += _counter(tags, "ImageCount", "Image count (all frames incl. electronic)", "total", "medium",
                      "Total images recorded by the body.")
    elif brand == "Pentax":
        c += _counter(tags, "ShutterCount", "Shutter count", "mechanical", "high")
    elif brand == "Fujifilm":
        c += _counter(tags, "ImageCount", "Image count", "total", "low",
                      "Fujifilm counter: may reset after firmware updates and rolls over above 32,767. Lower bound only.",
                      groups={"FujiFilm"})
    # generic fallback - any counter-like tag ExifTool decoded we have not mapped yet
    if not c:
        for key, v in tags.search(r"^(mechanical)?shuttercount\d?$|^releasecount$|^actuationcount$"):
            iv = _as_int(v)
            if iv:
                c.append({"label": key.split(":")[-1], "value": iv, "tag": key, "kind": "total",
                          "reliability": "low", "note": "Unmapped counter tag - verify by a second method."})
    return c


def primary_count(counters):
    order = {"mechanical": 0, "total": 1, "image": 2}
    rel = {"high": 0, "medium": 1, "low": 2}
    cand = [x for x in counters if x["kind"] in ("mechanical", "total")]
    if not cand:
        return None
    cand.sort(key=lambda x: (rel[x["reliability"]], order[x["kind"]], -x["value"]))
    return cand[0]


# -------------------------------------------------------------- ratings ----

def load_ratings(custom_path=None):
    with open(os.path.join(DATA, "ratings.json"), encoding="utf-8") as fh:
        base = json.load(fh)["models"]
    custom = []
    if custom_path and os.path.exists(custom_path):
        try:
            with open(custom_path, encoding="utf-8") as fh:
                custom = json.load(fh).get("models", [])
            for m in custom:
                m["confidence"] = m.get("confidence") or "store"
        except (OSError, ValueError):
            custom = []
    return custom + base


def find_rating(model, ratings):
    if not model:
        return None
    for r in ratings:
        try:
            if re.search(r["match"], model.strip(), re.I):
                return r
        except re.error:
            continue
    return None


def grade_for(pct):
    if pct is None:
        return None
    for limit, g in ((15, "A - Excellent"), (35, "B - Very good"), (60, "C - Good"),
                     (85, "D - Fair, priced for wear"), (100, "E - Near rated life")):
        if pct < limit:
            return g
    return "F - Beyond rated life"


def load_guides():
    with open(os.path.join(DATA, "guides.json"), encoding="utf-8") as fh:
        return json.load(fh)["brands"]


def guide_for(brand):
    for g in load_guides():
        if g["brand"].lower() == (brand or "").lower():
            return g
    return None


# ------------------------------------------------------------- analysis ----

def analyze(raw, ratings=None, now=None, display_name=None):
    now = now or _dt.datetime.now()
    tags = Tags(raw)
    make = tags.first("Make") or ""
    model = str(tags.first("Model", "CameraModelName", "UniqueCameraModel") or "").strip()
    file_type = tags.first("FileType") or ""
    brand = normalize_brand(make, model)
    if brand == "Unknown":
        brand = {"R3D": "RED", "BRAW": "Blackmagic Design", "ARI": "ARRI", "ARX": "ARRI"}.get(
            str(file_type).upper(), brand)

    serial = tags.first("SerialNumber", "BodySerialNumber", "CameraSerialNumber",
                        groups=None)
    internal = tags.first("InternalSerialNumber")
    lens_serial = tags.first("LensSerialNumber")
    lens = tags.first("LensModel", "LensType", "Lens", "LensID")
    firmware = tags.first("FirmwareVersion", "FirmwareRevision", "CanonFirmwareVersion",
                          "Software") or ""
    software = str(tags.first("Software", "CreatorTool", "ProcessingSoftware") or "")
    captured = _parse_dt(tags.first("DateTimeOriginal", "CreateDate", "DateTimeCreated",
                                    "DateCreated", "ModifyDate"))
    power_on = tags.first("PowerOnTime")
    since_power = tags.first("TimeSincePowerOn")

    is_clip = str(file_type).upper() in VIDEO_TYPES
    counters = [] if is_clip else extract_counters(brand, model, tags, file_type)
    primary = primary_count(counters)

    ratings = ratings if ratings is not None else load_ratings()
    rating = find_rating(model, ratings)
    pct = None
    grade = None
    if primary and rating and rating.get("rated"):
        pct = round(primary["value"] * 100.0 / rating["rated"], 1)
        grade = grade_for(pct)

    flags = []
    has_makernotes = bool(tags.groups() & MAKER_GROUPS)
    if not make and not model:
        flags.append(("fail", "No camera make/model in this file - it is not an original camera file."))
    elif is_clip:
        flags.append(("info", "Video clip: identifies the camera model and serial. Runtime / sensor hours "
                              "are not stored in clips - read them from the camera menu (see guide) and "
                              "enter them under Manual readings."))
    elif not has_makernotes and brand not in ("RED", "ARRI", "Blackmagic Design"):
        flags.append(("fail", "Manufacturer MakerNotes are missing. The file was exported/re-saved "
                              "by software, so counters were stripped. Use an untouched out-of-camera file."))
    if software and EDITING_SOFTWARE.search(software):
        flags.append(("warn", f"File was processed by software ('{software}'). Values may have been "
                              "altered - take a fresh shot on the store's card instead."))
    if captured:
        age = now - captured
        if age.days > 2:
            flags.append(("warn", f"Image was captured {captured:%Y-%m-%d} ({age.days} days ago per the "
                                  "camera clock). The count reflects that date - shoot a new frame at intake."))
        elif age.total_seconds() < -86400:
            flags.append(("warn", "Camera clock is set in the future - check the date/time setting."))
    if rating and rating.get("electronic_only"):
        flags.append(("info", f"{rating['name']} has no mechanical shutter - the count measures usage, "
                              "not shutter wear."))
    if primary and primary["reliability"] == "low":
        flags.append(("warn", "Counter for this brand is low-confidence. Confirm with service or "
                              "inspect physically."))
    if counters and not primary:
        flags.append(("warn", "Only a non-lifetime image counter was found."))
    if not counters and make and not is_clip:
        flags.append(("warn", f"No shutter/actuation counter is stored in {brand} files of this type. "
                              "See the procedure below."))
    if rating and rating.get("confidence") == "reported":
        flags.append(("info", f"Rated life for {rating['name']} is a reported figure, not a published "
                              "manufacturer rating."))
    if pct is not None and pct >= 85:
        flags.append(("warn", f"Shutter at {pct}% of rated life - budget for a shutter replacement."))
    if not serial:
        flags.append(("info", "No body serial in the file - record the serial from the body label manually."))

    return {
        "file": display_name or os.path.basename(str(raw.get("SourceFile", ""))),
        "file_type": file_type,
        "brand": brand,
        "make": make,
        "model": model,
        "display_name": pretty_name(brand, model, rating),
        "serial": re.sub(r"^\s*NO=\s*", "", str(serial)).strip() if serial is not None else None,
        "internal_serial": str(internal) if internal is not None else None,
        "lens": str(lens) if lens is not None else None,
        "lens_serial": str(lens_serial) if lens_serial is not None else None,
        "firmware": str(firmware) if firmware else None,
        "software": software or None,
        "captured_at": captured.isoformat(sep=" ") if captured else None,
        "power_on_time": str(power_on) if power_on else None,
        "time_since_power_on_s": since_power,
        "counters": counters,
        "primary": primary,
        "rating": rating,
        "life_used_pct": pct,
        "grade": grade,
        "flags": [{"level": l, "msg": m} for l, m in flags],
        "guide": guide_for(brand),
        "evidence_tags": {k: v for k, v in raw.items()
                          if re.search(r"count|serial|model|make|firmware|software|date|poweron",
                                       k, re.I) and not isinstance(v, (dict, list))
                          and len(str(v)) < 200},
    }


def cross_check(results):
    """Compare several files from the same body - catches swapped files and stale shots."""
    flags = []
    ok = [r for r in results if r.get("model")]
    serials = {r["serial"] for r in ok if r.get("serial")}
    models = {r["model"] for r in ok}
    if len(models) > 1:
        flags.append({"level": "fail", "msg": "Files come from different camera models: " + ", ".join(sorted(models))})
    if len(serials) > 1:
        flags.append({"level": "fail", "msg": "Files carry different serial numbers: " + ", ".join(sorted(serials))})
    seq = [(r["captured_at"], r["primary"]["value"]) for r in ok if r.get("primary") and r.get("captured_at")]
    seq.sort()
    for (t1, c1), (t2, c2) in zip(seq, seq[1:]):
        if c2 < c1:
            flags.append({"level": "warn", "msg": f"Counter went backwards ({c1:,} at {t1} -> {c2:,} at {t2}). "
                                                  "Possible counter reset, board replacement or tampered file."})
            break
    return flags


def compare_serial(found, expected):
    if not found or not expected:
        return None
    norm = lambda s: re.sub(r"[^0-9A-Z]", "", str(s).upper())
    f, e = norm(found), norm(expected)
    if not f or not e:
        return None
    return f == e or f.endswith(e) or e.endswith(f)
