"""Windows transport: raw PTP through Windows Portable Devices (WPD) "MTP extension"
commands, the same mechanism used by Windows tethering tools. It works with the
stock Windows camera driver, so no driver replacement (Zadig/libusb) is needed.

Requires the 'comtypes' package (installed automatically by the Windows launcher).
"""
import ctypes
import os
import sys
from ctypes import POINTER, byref, c_ubyte, c_ulong, c_wchar_p, cast

_mods = None

MTP_EXT = "{4D545058-1A2E-4106-A357-771E0819FC56}"
COMMON = "{F0422A9C-5DC8-4440-B5BD-5DF28835658A}"
CLIENT = "{204D9F0C-2292-4080-9F42-40664E70F859}"

CMD_WITHOUT_DATA = 12
CMD_WITH_DATA_TO_READ = 13
CMD_READ_DATA = 15
CMD_END_DATA_TRANSFER = 17

P_OPCODE, P_PARAMS, P_RESP_CODE, P_RESP_PARAMS = 1001, 1002, 1003, 1004
P_CONTEXT, P_TOTAL_SIZE, P_BYTES_TO_READ, P_BYTES_READ = 1006, 1007, 1008, 1009
P_DATA, P_OPTIMAL_BUF = 1012, 1013

VT_UI4 = 19
CHUNK = 1 << 20


def available():
    if os.name != "nt":
        return False, "Windows only"
    try:
        _load()
        return True, None
    except Exception as ex:  # noqa: BLE001
        return False, f"{type(ex).__name__}: {ex}"


def _load():
    global _mods
    if _mods:
        return _mods
    if "comtypes" not in sys.modules:
        sys.coinit_flags = 0  # COINIT_MULTITHREADED: requests run on worker threads
    import comtypes
    import comtypes.client
    # the .exe build ships pre-generated wrappers in comtypes.gen
    api = comtypes.client.GetModule("portabledeviceapi.dll")
    types = comtypes.client.GetModule("portabledevicetypes.dll")
    _mods = (comtypes, comtypes.client, api, types)
    return _mods


_ARG_TYPES = {}


def _arg_struct(obj, method, index):
    """The ctypes structure a COM method expects for argument `index`.

    Windows ships two type libraries (PortableDeviceApi and PortableDeviceTypes) that both
    declare PROPERTYKEY / PROPVARIANT, so comtypes generates two distinct Python classes.
    An object returned by the device may use either set, so ask the interface itself."""
    ck = (type(obj), method, index)
    if ck in _ARG_TYPES:
        return _ARG_TYPES[ck]
    iface = getattr(type(obj), "_type_", None) or type(obj)
    found = None
    for klass in getattr(iface, "__mro__", ()):
        for m in klass.__dict__.get("_methods_", None) or []:
            try:
                name, argtypes = m[1], m[2]
            except (IndexError, TypeError):
                continue
            if name == method and len(argtypes) > index:
                found = getattr(argtypes[index], "_type_", None)
                break
        if found is not None:
            break
    _ARG_TYPES[ck] = found
    return found


def _key(types, guid, pid, obj=None, method="SetUnsignedIntegerValue"):
    from comtypes import GUID
    cls = (_arg_struct(obj, method, 0) if obj is not None else None) or getattr(types, "_tagpropertykey")
    k = cls()
    k.fmtid = GUID(guid)
    k.pid = pid
    return k


def _new(cc, comtypes, coclass, iface):
    return cc.CreateObject(coclass, clsctx=comtypes.CLSCTX_INPROC_SERVER, interface=iface)


def _pv_new(types, coll=None, method="Add", index=0):
    cls = (_arg_struct(coll, method, index) if coll is not None else None) or types.tag_inner_PROPVARIANT
    pv = cls()
    ctypes.memset(ctypes.addressof(pv), 0, ctypes.sizeof(pv))
    return pv


def _pv_u4(types, value, coll=None):
    pv = _pv_new(types, coll)
    ctypes.memmove(ctypes.addressof(pv), byref(ctypes.c_ushort(VT_UI4)), 2)
    ctypes.memmove(ctypes.addressof(pv) + 8, byref(ctypes.c_uint32(value)), 4)
    return pv


def _pv_read_u4(pv):
    return ctypes.c_uint32.from_address(ctypes.addressof(pv) + 8).value



# ---- struct marshalling -------------------------------------------------------
# Windows' PortableDeviceApi and PortableDeviceTypes type libraries both declare
# PROPERTYKEY and PROPVARIANT, so comtypes generates two distinct Python classes for
# each, and a given interface may want either one. Rather than guess, try every
# generated class and remember which one each (interface, method) accepts.

_PREF = {}


def _struct_classes(name):
    found = []
    for modname, mod in list(sys.modules.items()):
        if modname.startswith("comtypes.gen") and mod is not None:
            c = getattr(mod, name, None)
            if isinstance(c, type) and c not in found:
                found.append(c)
    return found


def _with_struct(obj, method, struct_name, index, make, before=(), after=()):
    """Call obj.method(*before, make(cls), *after) with the struct class it accepts."""
    fn = getattr(obj, method)
    pk = (type(obj), method, index)
    order = [c for c in (_PREF.get(pk), _arg_struct(obj, method, index)) if c is not None]
    order += [c for c in _struct_classes(struct_name) if c not in order]
    last = None
    for cls in order:
        arg = make(cls)
        try:
            result = fn(*before, arg, *after)
        except ctypes.ArgumentError as ex:
            last = ex
            continue
        _PREF[pk] = cls
        return result, arg
    raise last or TypeError(f"no {struct_name} class accepted by {method}")


def kv(obj, method, guid, pid, *args):
    """IPortableDeviceValues.<method>(PROPERTYKEY(guid, pid), *args)."""
    from comtypes import GUID

    def make(cls):
        k = cls()
        k.fmtid = GUID(guid)
        k.pid = pid
        return k
    return _with_struct(obj, method, "_tagpropertykey", 0, make, after=args)[0]


def _zero(cls):
    v = cls()
    ctypes.memset(ctypes.addressof(v), 0, ctypes.sizeof(v))
    return v


def pv_add_u4(coll, value):
    def make(cls):
        pv = _zero(cls)
        ctypes.memmove(ctypes.addressof(pv), byref(ctypes.c_ushort(VT_UI4)), 2)
        ctypes.memmove(ctypes.addressof(pv) + 8, byref(ctypes.c_uint32(value & 0xFFFFFFFF)), 4)
        return pv
    _with_struct(coll, "Add", "tag_inner_PROPVARIANT", 0, make)


def pv_get_u4(coll, i):
    _, ptr = _with_struct(coll, "GetAt", "tag_inner_PROPVARIANT", 1, lambda cls: ctypes.pointer(_zero(cls)), before=(i,))
    return _pv_read_u4(ptr.contents)


def coll_count(coll):
    """IPortableDevicePropVariantCollection::GetCount - the typelib declares the
    count as an [in] pointer, so comtypes needs one passed explicitly."""
    n = c_ulong(0)
    try:
        coll.GetCount(ctypes.pointer(n))
        return n.value
    except TypeError:
        return coll.GetCount()


def list_devices():
    """Return [(pnp_id, friendly_name)] for every WPD device (cameras, phones, players)."""
    comtypes, cc, api, types = _load()
    mgr = _new(cc, comtypes, api.PortableDeviceManager, api.IPortableDeviceManager)
    try:
        mgr.RefreshDeviceList()
    except Exception:  # noqa: BLE001
        pass
    count = c_ulong(0)
    mgr.GetDevices(POINTER(c_wchar_p)(), ctypes.pointer(count))
    if not count.value:
        return []
    ids = (c_wchar_p * count.value)()
    mgr.GetDevices(cast(ids, POINTER(c_wchar_p)), ctypes.pointer(count))
    out = []
    for pnp in ids[:count.value]:
        name = pnp
        try:
            n = c_ulong(0)
            mgr.GetDeviceFriendlyName(pnp, POINTER(ctypes.c_ushort)(), ctypes.pointer(n))
            buf = ctypes.create_unicode_buffer(n.value + 1)
            mgr.GetDeviceFriendlyName(pnp, cast(buf, POINTER(ctypes.c_ushort)), ctypes.pointer(n))
            name = buf.value or pnp
        except Exception:  # noqa: BLE001
            pass
        out.append((pnp, name))
    return out


class WPDTransport:
    def __init__(self, pnp_id):
        comtypes, cc, api, types = _load()
        self.comtypes, self.cc, self.api, self.types = comtypes, cc, api, types
        self.dev = _new(cc, comtypes, api.PortableDevice, api.IPortableDevice)
        client = self._values()
        kv(client, "SetStringValue", CLIENT, 2, "Camera Check")
        kv(client, "SetUnsignedIntegerValue", CLIENT, 3, 1)
        kv(client, "SetUnsignedIntegerValue", CLIENT, 4, 0)
        kv(client, "SetUnsignedIntegerValue", CLIENT, 5, 0)
        kv(client, "SetUnsignedIntegerValue", CLIENT, 8, 0x20000)  # SECURITY_IMPERSONATION
        self.dev.Open(pnp_id, client)

    def close(self):
        try:
            self.dev.Close()
        except Exception:  # noqa: BLE001
            pass

    # helpers ---------------------------------------------------------------
    def _values(self):
        return _new(self.cc, self.comtypes, self.types.PortableDeviceValues, self.types.IPortableDeviceValues)

    def _command(self, cmd_pid, setup):
        p = self._values()
        from comtypes import GUID
        kv(p, "SetGuidValue", COMMON, 1001, GUID(MTP_EXT))
        kv(p, "SetUnsignedIntegerValue", COMMON, 1002, cmd_pid)
        setup(p)
        res = self.dev.SendCommand(0, p)
        try:
            hr = kv(res, "GetErrorValue", COMMON, 1003)
        except Exception:  # noqa: BLE001
            hr = 0
        if hr and hr & 0x80000000:
            raise OSError(f"WPD command {cmd_pid} failed, HRESULT 0x{hr & 0xFFFFFFFF:08x}")
        return res

    def _op_params(self, p, opcode, params):
        kv(p, "SetUnsignedIntegerValue", MTP_EXT, P_OPCODE, opcode)
        coll = _new(self.cc, self.comtypes, self.types.PortableDevicePropVariantCollection,
                    self.types.IPortableDevicePropVariantCollection)
        for v in params:
            pv_add_u4(coll, v)
        kv(p, "SetIPortableDevicePropVariantCollectionValue", MTP_EXT, P_PARAMS, coll)

    def _response(self, res):
        code = kv(res, "GetUnsignedIntegerValue", MTP_EXT, P_RESP_CODE)
        params = []
        try:
            coll = kv(res, "GetIPortableDevicePropVariantCollectionValue", MTP_EXT, P_RESP_PARAMS)
            n = coll_count(coll)
            for i in range(n):
                params.append(pv_get_u4(coll, i))
        except Exception:  # noqa: BLE001
            pass
        return code, params

    # transport API -----------------------------------------------------------
    def transaction(self, opcode, params, read_data=False):
        if not read_data:
            res = self._command(CMD_WITHOUT_DATA, lambda p: self._op_params(p, opcode, params))
            code, rparams = self._response(res)
            return code, rparams, b""
        res = self._command(CMD_WITH_DATA_TO_READ, lambda p: self._op_params(p, opcode, params))
        ctx = kv(res, "GetStringValue", MTP_EXT, P_CONTEXT)
        total = kv(res, "GetUnsignedLargeIntegerValue", MTP_EXT, P_TOTAL_SIZE)
        try:
            chunk = max(64 * 1024, min(CHUNK * 4, kv(res, "GetUnsignedIntegerValue", MTP_EXT, P_OPTIMAL_BUF)))
        except Exception:  # noqa: BLE001
            chunk = CHUNK
        data = bytearray()
        try:
            while len(data) < total:
                want = min(chunk, total - len(data))
                buf = (c_ubyte * want)()

                def setup(p, want=want, buf=buf):
                    kv(p, "SetStringValue", MTP_EXT, P_CONTEXT, ctx)
                    kv(p, "SetUnsignedIntegerValue", MTP_EXT, P_BYTES_TO_READ, want)
                    kv(p, "SetBufferValue", MTP_EXT, P_DATA, cast(buf, POINTER(c_ubyte)), want)

                r = self._command(CMD_READ_DATA, setup)
                got = kv(r, "GetUnsignedIntegerValue", MTP_EXT, P_BYTES_READ)
                ptr, n = kv(r, "GetBufferValue", MTP_EXT, P_DATA)
                n = min(n, got) if got else n
                if not n:
                    break
                data += ctypes.string_at(ptr, n)
                try:
                    ctypes.windll.ole32.CoTaskMemFree(ptr)
                except Exception:  # noqa: BLE001
                    pass
        finally:
            end = self._command(CMD_END_DATA_TRANSFER,
                                lambda p: kv(p, "SetStringValue", MTP_EXT, P_CONTEXT, ctx))
        code, rparams = self._response(end)
        return code, rparams, bytes(data)
