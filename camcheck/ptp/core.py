"""Transport-independent PTP (ISO 15740) session: datasets, properties, objects,
and vendor shutter-counter readouts.

A transport implements:
    transaction(opcode, params, read_data=False) -> (response_code, response_params, data_bytes)
"""
import struct

# --- standard operation codes ------------------------------------------------
OC_GetDeviceInfo = 0x1001
OC_GetStorageIDs = 0x1004
OC_GetObjectHandles = 0x1007
OC_GetObjectInfo = 0x1008
OC_GetObject = 0x1009
OC_GetDevicePropDesc = 0x1014
OC_GetDevicePropValue = 0x1015
OC_GetPartialObject = 0x101B

RC_OK = 0x2001

# --- vendor codes (from libgphoto2 ptp.h) -------------------------------------
OC_CANON_EOS_SetRemoteMode = 0x9114
OC_CANON_EOS_SetEventMode = 0x9115
OC_CANON_EOS_GetEvent = 0x9116
OC_CANON_EOS_RequestDevicePropValue = 0x9127
EC_CANON_EOS_PropValueChanged = 0xC189
DPC_CANON_EOS_ShutterReleaseCounter = 0xD167
DPC_CANON_EOS_ShutterCounter = 0xD1AC
DPC_CANON_EOS_SerialNumber = 0xD1AF
DPC_FUJI_TotalShotCount = 0xD310
DPC_OLYMPUS_ShutterActuationCount = 0xD059

# data type codes
_DT = {0x0001: "b", 0x0002: "B", 0x0003: "h", 0x0004: "H", 0x0005: "i", 0x0006: "I",
       0x0007: "q", 0x0008: "Q"}

IMAGE_EXT = (".jpg", ".jpeg", ".hif", ".heif", ".heic", ".nef", ".nrw", ".cr2", ".cr3", ".crw",
             ".arw", ".srf", ".sr2", ".raf", ".orf", ".ori", ".rw2", ".rwl", ".pef", ".dng",
             ".3fr", ".fff", ".iiq", ".x3f", ".srw")
JPEG_EXT = (".jpg", ".jpeg")


class PTPError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


# --- dataset parsing ---------------------------------------------------------

class Reader:
    def __init__(self, data, pos=0):
        self.d, self.p = data, pos

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def _fmt(self, f):
        n = struct.calcsize("<" + f)
        v = struct.unpack_from("<" + f, self.d, self.p)[0]
        self.p += n
        return v

    def u16(self): return self._fmt("H")
    def u32(self): return self._fmt("I")
    def u64(self): return self._fmt("Q")

    def string(self):
        n = self.u8()
        if n == 0:
            return ""
        raw = self.d[self.p:self.p + n * 2]
        self.p += n * 2
        return raw.decode("utf-16-le", "replace").rstrip("\x00")

    def array16(self):
        n = self.u32()
        out = list(struct.unpack_from(f"<{n}H", self.d, self.p))
        self.p += 2 * n
        return out

    def array32(self):
        n = self.u32()
        out = list(struct.unpack_from(f"<{n}I", self.d, self.p))
        self.p += 4 * n
        return out

    def value(self, dtype):
        if dtype == 0xFFFF:
            return self.string()
        f = _DT.get(dtype)
        if f is None:
            raise PTPError(f"unsupported data type 0x{dtype:04x}")
        return self._fmt(f)


def parse_device_info(data):
    r = Reader(data)
    di = {"standard_version": r.u16(), "vendor_ext_id": r.u32(), "vendor_ext_version": r.u16(),
          "vendor_ext_desc": r.string(), "functional_mode": r.u16(),
          "operations": r.array16(), "events": r.array16(), "properties": r.array16(),
          "capture_formats": r.array16(), "image_formats": r.array16()}
    di["manufacturer"] = r.string()
    di["model"] = r.string()
    di["device_version"] = r.string()
    di["serial"] = r.string()
    return di


def parse_object_info(data):
    r = Reader(data)
    oi = {"storage_id": r.u32(), "format": r.u16(), "protection": r.u16(), "size": r.u32(),
          "thumb_format": r.u16(), "thumb_size": r.u32(), "thumb_w": r.u32(), "thumb_h": r.u32(),
          "image_w": r.u32(), "image_h": r.u32(), "bit_depth": r.u32(), "parent": r.u32(),
          "assoc_type": r.u16(), "assoc_desc": r.u32(), "sequence": r.u32()}
    oi["filename"] = r.string()
    oi["capture_date"] = r.string()
    oi["modification_date"] = r.string()
    return oi


def parse_canon_eos_events(data):
    """Return {propcode: raw value bytes} from a Canon EOS GetEvent payload."""
    props = {}
    pos = 0
    while pos + 8 <= len(data):
        size, code = struct.unpack_from("<II", data, pos)
        if size < 8 or code == 0:
            break
        if code == EC_CANON_EOS_PropValueChanged and size >= 12:
            dpc = struct.unpack_from("<I", data, pos + 8)[0]
            props[dpc] = bytes(data[pos + 12:pos + size])
        pos += size
    return props


# --- session -----------------------------------------------------------------

class Session:
    def __init__(self, transport):
        self.t = transport
        self.info = None
        self.log = []

    def _tx(self, op, params=(), read=False, ok_only=True):
        rc, rparams, data = self.t.transaction(op, list(params), read)
        self.log.append(f"op 0x{op:04x} {list(map(hex, params))} -> 0x{rc:04x} ({len(data or b'')} bytes)")
        if ok_only and rc != RC_OK:
            raise PTPError(f"PTP operation 0x{op:04x} failed with 0x{rc:04x}", rc)
        return rc, rparams, data or b""

    def device_info(self):
        _, _, data = self._tx(OC_GetDeviceInfo, read=True)
        self.info = parse_device_info(data)
        return self.info

    def supports_op(self, op):
        return bool(self.info) and op in self.info["operations"]

    def supports_prop(self, prop):
        return bool(self.info) and prop in self.info["properties"]

    def prop_value(self, prop, dtype):
        _, _, data = self._tx(OC_GetDevicePropValue, [prop], read=True)
        return Reader(data).value(dtype)

    def storage_ids(self):
        _, _, data = self._tx(OC_GetStorageIDs, read=True)
        return [s for s in Reader(data).array32() if s & 0xFFFF]  # skip empty slots

    def object_handles(self, storage):
        _, _, data = self._tx(OC_GetObjectHandles, [storage, 0, 0], read=True)
        return Reader(data).array32()

    def object_info(self, handle):
        _, _, data = self._tx(OC_GetObjectInfo, [handle], read=True)
        return parse_object_info(data)

    def get_object(self, handle, max_bytes=None, size=None):
        if max_bytes and size and size > max_bytes and self.supports_op(OC_GetPartialObject):
            _, _, data = self._tx(OC_GetPartialObject, [handle, 0, max_bytes], read=True)
            return data, True
        _, _, data = self._tx(OC_GetObject, [handle], read=True)
        return data, False

    # --- vendor counters ----------------------------------------------------
    def canon_eos_counters(self):
        out = {}
        if not self.supports_op(OC_CANON_EOS_GetEvent):
            return out
        try:
            if self.supports_op(OC_CANON_EOS_SetRemoteMode):
                self._tx(OC_CANON_EOS_SetRemoteMode, [1])
            self._tx(OC_CANON_EOS_SetEventMode, [1])
            props = {}
            for _ in range(3):
                props.update(parse_canon_eos_events(self._tx(OC_CANON_EOS_GetEvent, read=True)[2]))
            if self.supports_op(OC_CANON_EOS_RequestDevicePropValue):
                for p in (DPC_CANON_EOS_ShutterCounter, DPC_CANON_EOS_SerialNumber):
                    self._tx(OC_CANON_EOS_RequestDevicePropValue, [p], ok_only=False)
                for _ in range(3):
                    props.update(parse_canon_eos_events(self._tx(OC_CANON_EOS_GetEvent, read=True)[2]))
            for code, label in ((DPC_CANON_EOS_ShutterCounter, "Shutter counter (Canon EOS)"),
                                (DPC_CANON_EOS_ShutterReleaseCounter, "Shutter release counter (Canon EOS)")):
                raw = props.get(code)
                if raw and len(raw) >= 4:
                    v = struct.unpack_from("<I", raw)[0]
                    if 0 < v < 50_000_000:
                        out[label] = v
            sn = props.get(DPC_CANON_EOS_SerialNumber)
            if sn:
                out["_serial"] = sn.split(b"\x00")[0].decode("ascii", "replace").strip()
        finally:
            try:
                self._tx(OC_CANON_EOS_SetEventMode, [0], ok_only=False)
                if self.supports_op(OC_CANON_EOS_SetRemoteMode):
                    self._tx(OC_CANON_EOS_SetRemoteMode, [0], ok_only=False)
            except Exception:  # noqa: BLE001 - best effort cleanup
                pass
        return out

    def vendor_counters(self):
        """Read every shutter counter the body exposes over PTP. Never raises."""
        out = {}
        maker = (self.info or {}).get("manufacturer", "").lower()
        errors = []
        try:
            if "canon" in maker:
                out.update(self.canon_eos_counters())
        except Exception as ex:  # noqa: BLE001
            errors.append(f"Canon EOS events: {ex}")
        for prop, label, conv in (
                (DPC_FUJI_TotalShotCount, "Total shot count (Fujifilm)", lambda v: v >> 16),
                (DPC_OLYMPUS_ShutterActuationCount, "Shutter actuation count (Olympus/OM)", lambda v: v)):
            if self.supports_prop(prop) and (("fuji" in maker) == (prop == DPC_FUJI_TotalShotCount)):
                try:
                    v = conv(self.prop_value(prop, 0x0006))
                    if v > 0:
                        out[label] = v
                except Exception as ex:  # noqa: BLE001
                    errors.append(f"prop 0x{prop:04x}: {ex}")
        if errors:
            out["_errors"] = errors
        return out

    # --- newest image -------------------------------------------------------
    def newest_images(self, scan_last=60):
        """Return ObjectInfo dicts (with 'handle') for the most recent image files."""
        found = []
        for sid in self.storage_ids():
            try:
                handles = self.object_handles(sid)
            except PTPError as ex:
                self.log.append(f"storage 0x{sid:08x}: {ex}")
                continue
            for h in sorted(handles)[-scan_last:]:
                try:
                    oi = self.object_info(h)
                except PTPError:
                    continue
                if oi["filename"].lower().endswith(IMAGE_EXT):
                    oi["handle"] = h
                    found.append(oi)
        found.sort(key=lambda o: (o["capture_date"] or "", o["handle"]), reverse=True)
        return found

    def pick_download(self, images):
        """Prefer a JPEG from the newest capture moment; otherwise the newest RAW."""
        if not images:
            return None
        newest = images[0]["capture_date"][:15]
        same = [o for o in images if o["capture_date"][:15] == newest]
        jpg = [o for o in same if o["filename"].lower().endswith(JPEG_EXT)]
        return (jpg or same or images)[0]
