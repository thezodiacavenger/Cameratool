"""Windows-only: exercise the WPD/COM marshalling used for USB camera access
(no camera needed). Skipped on other platforms."""
import ctypes
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@unittest.skipUnless(os.name == "nt", "Windows only")
class WPDMarshalling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from camcheck.ptp import wpd
        cls.wpd = wpd
        cls.comtypes, cls.cc, cls.api, cls.types = wpd._load()

    def values(self):
        return self.wpd._new(self.cc, self.comtypes, self.types.PortableDeviceValues,
                             self.types.IPortableDeviceValues)

    def test_scalar_roundtrip(self):
        v = self.values()
        self.wpd.kv(v, "SetUnsignedIntegerValue", self.wpd.MTP_EXT, 1001, 0x1015)
        self.assertEqual(self.wpd.kv(v, "GetUnsignedIntegerValue", self.wpd.MTP_EXT, 1001), 0x1015)
        self.wpd.kv(v, "SetStringValue", self.wpd.MTP_EXT, 1006, "ctx-123")
        self.assertEqual(self.wpd.kv(v, "GetStringValue", self.wpd.MTP_EXT, 1006), "ctx-123")
        from comtypes import GUID
        self.wpd.kv(v, "SetGuidValue", self.wpd.COMMON, 1001, GUID(self.wpd.MTP_EXT))

    def test_param_collection(self):
        coll = self.wpd._new(self.cc, self.comtypes, self.types.PortableDevicePropVariantCollection,
                             self.types.IPortableDevicePropVariantCollection)
        for x in (0xD1AC, 0xFFFFFFFF, 0):
            self.wpd.pv_add_u4(coll, x)
        self.assertEqual(self.wpd.coll_count(coll), 3)
        v = self.values()
        self.wpd.kv(v, "SetIPortableDevicePropVariantCollectionValue", self.wpd.MTP_EXT, 1002, coll)
        back = self.wpd.kv(v, "GetIPortableDevicePropVariantCollectionValue", self.wpd.MTP_EXT, 1002)
        out = [self.wpd.pv_get_u4(back, i) for i in range(self.wpd.coll_count(back))]
        self.assertEqual(out, [0xD1AC, 0xFFFFFFFF, 0])

    def test_buffer_roundtrip(self):
        v = self.values()
        data = bytes(range(256)) * 4
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        self.wpd.kv(v, "SetBufferValue", self.wpd.MTP_EXT, 1012, ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)), len(data))
        ptr, n = self.wpd.kv(v, "GetBufferValue", self.wpd.MTP_EXT, 1012)
        self.assertEqual(ctypes.string_at(ptr, n), data)

    def test_cross_library_keys(self):
        """Objects typed by either type library must accept our keys (the bug a real device hit)."""
        for lib in (self.types, self.api):
            iface = getattr(lib, "IPortableDeviceValues", None)
            if iface is None:
                continue
            v = self.wpd._new(self.cc, self.comtypes, self.types.PortableDeviceValues, iface)
            self.wpd.kv(v, "SetUnsignedIntegerValue", self.wpd.MTP_EXT, 1001, 7)
            self.assertEqual(self.wpd.kv(v, "GetUnsignedIntegerValue", self.wpd.MTP_EXT, 1001), 7)
            self.wpd.kv(v, "SetStringValue", self.wpd.CLIENT, 2, "Camera Check")
            self.assertEqual(self.wpd.kv(v, "GetStringValue", self.wpd.CLIENT, 2), "Camera Check")
        for lib in (self.types, self.api):
            iface = getattr(lib, "IPortableDevicePropVariantCollection", None)
            if iface is None:
                continue
            c = self.wpd._new(self.cc, self.comtypes, self.types.PortableDevicePropVariantCollection, iface)
            self.wpd.pv_add_u4(c, 42)
            self.wpd.pv_add_u4(c, 0xFFFFFFFF)
            self.assertEqual([self.wpd.pv_get_u4(c, i) for i in range(self.wpd.coll_count(c))], [42, 0xFFFFFFFF])

    def test_transport_opens_any_device(self):
        """If the machine has any WPD device, opening it must not raise a ctypes TypeError."""
        devs = self.wpd.list_devices()
        if not devs:
            self.skipTest("no WPD devices on this machine")
        try:
            t = self.wpd.WPDTransport(devs[0][0])
            t.close()
        except TypeError as ex:
            self.fail(f"marshalling bug: {ex}")
        except Exception:  # noqa: BLE001 - device may refuse; that's fine
            pass

    def test_list_devices_runs(self):
        devs = self.wpd.list_devices()
        self.assertIsInstance(devs, list)


if __name__ == "__main__":
    unittest.main()
