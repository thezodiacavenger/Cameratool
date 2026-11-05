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


def _key(types, guid, pid):
    from comtypes import GUID
    cls = getattr(types, "_tagpropertykey")
    k = cls()
    k.fmtid = GUID(guid)
    k.pid = pid
    return k


def _new(cc, comtypes, coclass, iface):
    return cc.CreateObject(coclass, clsctx=comtypes.CLSCTX_INPROC_SERVER, interface=iface)


def _pv_u4(types, value):
    pv = types.tag_inner_PROPVARIANT()
    ctypes.memset(byref(pv), 0, ctypes.sizeof(pv))
    ctypes.memmove(byref(pv), byref(ctypes.c_ushort(VT_UI4)), 2)
    ctypes.memmove(ctypes.addressof(pv) + 8, byref(ctypes.c_uint32(value)), 4)
    return pv


def _pv_read_u4(pv):
    return ctypes.c_uint32.from_address(ctypes.addressof(pv) + 8).value


def list_devices():
    """Return [(pnp_id, friendly_name)] for every WPD device (cameras, phones, players)."""
    comtypes, cc, api, types = _load()
    mgr = _new(cc, comtypes, api.PortableDeviceManager, api.IPortableDeviceManager)
    try:
        mgr.RefreshDeviceList()
    except Exception:  # noqa: BLE001
        pass
    count = c_ulong(0)
    mgr.GetDevices(POINTER(c_wchar_p)(), byref(count))
    if not count.value:
        return []
    ids = (c_wchar_p * count.value)()
    mgr.GetDevices(cast(ids, POINTER(c_wchar_p)), byref(count))
    out = []
    for pnp in ids[:count.value]:
        name = pnp
        try:
            n = c_ulong(0)
            mgr.GetDeviceFriendlyName(pnp, POINTER(ctypes.c_ushort)(), byref(n))
            buf = ctypes.create_unicode_buffer(n.value + 1)
            mgr.GetDeviceFriendlyName(pnp, cast(buf, POINTER(ctypes.c_ushort)), byref(n))
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
        client.SetStringValue(_key(types, CLIENT, 2), "Camera Check")
        client.SetUnsignedIntegerValue(_key(types, CLIENT, 3), 1)
        client.SetUnsignedIntegerValue(_key(types, CLIENT, 4), 0)
        client.SetUnsignedIntegerValue(_key(types, CLIENT, 5), 0)
        client.SetUnsignedIntegerValue(_key(types, CLIENT, 8), 0x20000)  # SECURITY_IMPERSONATION
        self.dev.Open(pnp_id, client)

    def close(self):
        try:
            self.dev.Close()
        except Exception:  # noqa: BLE001
            pass

    # helpers ---------------------------------------------------------------
    def _values(self):
        return _new(self.cc, self.comtypes, self.types.PortableDeviceValues, self.types.IPortableDeviceValues)

    def _k(self, guid, pid):
        return _key(self.types, guid, pid)

    def _command(self, cmd_pid, setup):
        p = self._values()
        from comtypes import GUID
        p.SetGuidValue(self._k(COMMON, 1001), GUID(MTP_EXT))
        p.SetUnsignedIntegerValue(self._k(COMMON, 1002), cmd_pid)
        setup(p)
        res = self.dev.SendCommand(0, p)
        try:
            hr = res.GetErrorValue(self._k(COMMON, 1003))
        except Exception:  # noqa: BLE001
            hr = 0
        if hr and hr & 0x80000000:
            raise OSError(f"WPD command {cmd_pid} failed, HRESULT 0x{hr & 0xFFFFFFFF:08x}")
        return res

    def _op_params(self, p, opcode, params):
        p.SetUnsignedIntegerValue(self._k(MTP_EXT, P_OPCODE), opcode)
        coll = _new(self.cc, self.comtypes, self.types.PortableDevicePropVariantCollection,
                    self.types.IPortableDevicePropVariantCollection)
        for v in params:
            coll.Add(_pv_u4(self.types, v & 0xFFFFFFFF))
        p.SetIPortableDevicePropVariantCollectionValue(self._k(MTP_EXT, P_PARAMS), coll)

    def _response(self, res):
        code = res.GetUnsignedIntegerValue(self._k(MTP_EXT, P_RESP_CODE))
        params = []
        try:
            coll = res.GetIPortableDevicePropVariantCollectionValue(self._k(MTP_EXT, P_RESP_PARAMS))
            n = coll.GetCount()
            for i in range(n):
                pv = self.types.tag_inner_PROPVARIANT()
                coll.GetAt(i, byref(pv))
                params.append(_pv_read_u4(pv))
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
        ctx = res.GetStringValue(self._k(MTP_EXT, P_CONTEXT))
        total = res.GetUnsignedLargeIntegerValue(self._k(MTP_EXT, P_TOTAL_SIZE))
        try:
            chunk = max(64 * 1024, min(CHUNK * 4, res.GetUnsignedIntegerValue(self._k(MTP_EXT, P_OPTIMAL_BUF))))
        except Exception:  # noqa: BLE001
            chunk = CHUNK
        data = bytearray()
        try:
            while len(data) < total:
                want = min(chunk, total - len(data))
                buf = (c_ubyte * want)()

                def setup(p, want=want, buf=buf):
                    p.SetStringValue(self._k(MTP_EXT, P_CONTEXT), ctx)
                    p.SetUnsignedIntegerValue(self._k(MTP_EXT, P_BYTES_TO_READ), want)
                    p.SetBufferValue(self._k(MTP_EXT, P_DATA), cast(buf, POINTER(c_ubyte)), want)

                r = self._command(CMD_READ_DATA, setup)
                got = r.GetUnsignedIntegerValue(self._k(MTP_EXT, P_BYTES_READ))
                ptr, n = r.GetBufferValue(self._k(MTP_EXT, P_DATA))
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
                                lambda p: p.SetStringValue(self._k(MTP_EXT, P_CONTEXT), ctx))
        code, rparams = self._response(end)
        return code, rparams, bytes(data)
