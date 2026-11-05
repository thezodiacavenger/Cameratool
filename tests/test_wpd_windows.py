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

    def k(self, pid):
        return self.wpd._key(self.types, self.wpd.MTP_EXT, pid)

    def test_scalar_roundtrip(self):
        v = self.values()
        v.SetUnsignedIntegerValue(self.k(1001), 0x1015)
        self.assertEqual(v.GetUnsignedIntegerValue(self.k(1001)), 0x1015)
        v.SetStringValue(self.k(1006), "ctx-123")
        self.assertEqual(v.GetStringValue(self.k(1006)), "ctx-123")
        from comtypes import GUID
        v.SetGuidValue(self.wpd._key(self.types, self.wpd.COMMON, 1001), GUID(self.wpd.MTP_EXT))

    def test_param_collection(self):
        coll = self.wpd._new(self.cc, self.comtypes, self.types.PortableDevicePropVariantCollection,
                             self.types.IPortableDevicePropVariantCollection)
        for x in (0xD1AC, 0xFFFFFFFF, 0):
            coll.Add(self.wpd._pv_u4(self.types, x))
        self.assertEqual(self.wpd.coll_count(coll), 3)
        v = self.values()
        v.SetIPortableDevicePropVariantCollectionValue(self.k(1002), coll)
        back = v.GetIPortableDevicePropVariantCollectionValue(self.k(1002))
        out = []
        for i in range(self.wpd.coll_count(back)):
            pv = self.types.tag_inner_PROPVARIANT()
            back.GetAt(i, ctypes.pointer(pv))
            out.append(self.wpd._pv_read_u4(pv))
        self.assertEqual(out, [0xD1AC, 0xFFFFFFFF, 0])

    def test_buffer_roundtrip(self):
        v = self.values()
        data = bytes(range(256)) * 4
        buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        v.SetBufferValue(self.k(1012), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)), len(data))
        ptr, n = v.GetBufferValue(self.k(1012))
        self.assertEqual(ctypes.string_at(ptr, n), data)

    def test_list_devices_runs(self):
        devs = self.wpd.list_devices()
        self.assertIsInstance(devs, list)


if __name__ == "__main__":
    unittest.main()
