"""Protocol tests against a simulated PTP camera, plus card-layout detection."""
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from camcheck import drives, usb  # noqa: E402
from camcheck.ptp import core  # noqa: E402


def pstr(s):
    if not s:
        return b"\x00"
    s += "\x00"
    return bytes([len(s)]) + s.encode("utf-16-le")


def a16(xs):
    return struct.pack("<I", len(xs)) + b"".join(struct.pack("<H", x) for x in xs)


def a32(xs):
    return struct.pack("<I", len(xs)) + b"".join(struct.pack("<I", x) for x in xs)


def device_info(maker, model, serial, ops, props):
    return (struct.pack("<HIH", 100, 6, 100) + pstr("microsoft.com: 1.0;") + struct.pack("<H", 0)
            + a16(ops) + a16([]) + a16(props) + a16([]) + a16([0x3801]) + pstr(maker) + pstr(model)
            + pstr("V1.10") + pstr(serial))


def object_info(name, size, date, storage=0x00010001):
    return (struct.pack("<IHHI", storage, 0x3801, 0, size) + struct.pack("<HIII", 0, 0, 0, 0)
            + struct.pack("<IIIIHII", 0, 0, 0, 0, 0, 0, 0) + pstr(name) + pstr(date) + pstr(date))


def eos_event(dpc, value_bytes):
    body = struct.pack("<II", dpc, 0) if False else struct.pack("<I", dpc) + value_bytes
    return struct.pack("<II", 8 + len(body), core.EC_CANON_EOS_PropValueChanged) + body


class MockCamera:
    def __init__(self, maker, model, serial, vendor_ops=(), props=(), files=None, eos=None, prop_values=None):
        std = [0x1001, 0x1002, 0x1004, 0x1007, 0x1008, 0x1009, 0x1015, 0x101B]
        self.di = device_info(maker, model, serial, std + list(vendor_ops), list(props))
        self.files = files or {}
        self.eos = eos or {}
        self.prop_values = prop_values or {}
        self.requested = set()
        self.calls = []

    def transaction(self, op, params, read=False):
        self.calls.append(op)
        if op == 0x1001:
            return 0x2001, [], self.di
        if op == 0x1004:
            return 0x2001, [], a32([0x00010001, 0x00020000])  # 2nd slot empty
        if op == 0x1007:
            return 0x2001, [], a32(sorted(self.files))
        if op == 0x1008:
            n, data, date = self.files[params[0]]
            return 0x2001, [], object_info(n, len(data), date)
        if op == 0x1009:
            return 0x2001, [], self.files[params[0]][1]
        if op == 0x101B:
            return 0x2001, [], self.files[params[0]][1][params[1]:params[1] + params[2]]
        if op == 0x1015:
            if params[0] in self.prop_values:
                return 0x2001, [], struct.pack("<I", self.prop_values[params[0]])
            return 0x200A, [], b""
        if op in (core.OC_CANON_EOS_SetRemoteMode, core.OC_CANON_EOS_SetEventMode):
            return 0x2001, [], b""
        if op == core.OC_CANON_EOS_RequestDevicePropValue:
            self.requested.add(params[0])
            return 0x2001, [], b""
        if op == core.OC_CANON_EOS_GetEvent:
            data = b"".join(eos_event(k, v) for k, v in self.eos.items() if k in self.requested)
            return 0x2001, [], data + struct.pack("<II", 8, 0)
        return 0x2005, [], b""


def saver():
    store = {}

    def save(name, data):
        store[name] = data
        return "e" * 32, name, "/tmp/" + name
    return save, store


class PTPTests(unittest.TestCase):
    def test_device_info_roundtrip(self):
        s = core.Session(MockCamera("Nikon Corporation", "Z 6_2", "6012345"))
        di = s.device_info()
        self.assertEqual((di["manufacturer"], di["model"], di["serial"], di["device_version"]),
                         ("Nikon Corporation", "Z 6_2", "6012345", "V1.10"))

    def test_nikon_downloads_newest_jpeg(self):
        files = {1: ("DSC_0001.NEF", b"N" * 100, "20261104T101010"),
                 2: ("DSC_0002.NEF", b"R" * 100, "20261105T090000"),
                 3: ("DSC_0002.JPG", b"J" * 50, "20261105T090000"),
                 4: ("CLIP.MOV", b"M" * 10, "20261105T100000")}
        save, store = saver()
        s = core.Session(MockCamera("Nikon Corporation", "Z 6_2", "6012345", files=files))
        s.device_info()
        r = usb._session_readout(s, {"transport": "mock"}, save, True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["image"]["name"], "DSC_0002.JPG")
        self.assertEqual(store["DSC_0002.JPG"], b"J" * 50)
        self.assertEqual(r["counters"], {})
        self.assertIsNone(r["shutter_count"])

    def test_large_raw_partial(self):
        files = {7: ("_DSC0007.ARW", b"A" * (usb.MAX_RAW_BYTES + 100), "20261105T090000")}
        save, store = saver()
        s = core.Session(MockCamera("Sony Corporation", "ILCE-7M3", "", files=files))
        s.device_info()
        r = usb._session_readout(s, {}, save, True)
        self.assertTrue(r["image"]["partial"])
        self.assertEqual(len(store["_DSC0007.ARW"]), usb.MAX_RAW_BYTES)

    def test_canon_eos_counter_and_serial(self):
        cam = MockCamera("Canon Inc.", "Canon EOS 5D Mark IV", "",
                         vendor_ops=[0x9114, 0x9115, 0x9116, 0x9127],
                         eos={core.DPC_CANON_EOS_ShutterCounter: struct.pack("<I", 123456),
                              core.DPC_CANON_EOS_SerialNumber: b"012345678901\x00"})
        s = core.Session(cam)
        s.device_info()
        r = usb._session_readout(s, {}, saver()[0], False)
        self.assertEqual(r["shutter_count"], 123456)
        self.assertEqual(r["serial"], "012345678901")
        self.assertEqual(cam.calls[-1], 0x9114)  # remote mode released at the end

    def test_fuji_total_shot_count(self):
        cam = MockCamera("FUJIFILM", "X-T4", "FF123", props=[core.DPC_FUJI_TotalShotCount],
                         prop_values={core.DPC_FUJI_TotalShotCount: (45210 << 16) | 7})
        s = core.Session(cam)
        s.device_info()
        r = usb._session_readout(s, {}, saver()[0], False)
        self.assertEqual(r["counters"], {"Total shot count (Fujifilm)": 45210})

    def test_no_photos(self):
        s = core.Session(MockCamera("OLYMPUS", "E-M1MarkII", "BHN1"))
        s.device_info()
        r = usb._session_readout(s, {}, saver()[0], True)
        self.assertIn("No photos", r["image_note"])


class CardTests(unittest.TestCase):
    def mk(self, root, *paths):
        for p in paths:
            full = os.path.join(root, *p.split("/"))
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as fh:
                fh.write("x")

    def test_layouts(self):
        with tempfile.TemporaryDirectory() as t:
            sd, cfe, red, empty, fixed = (os.path.join(t, n) for n in ("sd", "cfe", "red", "empty", "fixed"))
            self.mk(sd, "DCIM/100NIKON/DSC_0001.JPG")
            self.mk(cfe, "PRIVATE/M4ROOT/CLIP/C0001.MP4", "PRIVATE/M4ROOT/CLIP/C0001M01.XML")
            self.mk(red, "A001_0101AB.RDM/A001_C001_0101AB.RDC/A001_C001_0101AB_001.R3D")
            os.makedirs(empty)
            self.mk(fixed, "Documents/a.txt")
            vols = [{"path": sd, "removable": True}, {"path": cfe, "removable": False},
                    {"path": red, "removable": False}, {"path": empty, "removable": True},
                    {"path": fixed, "removable": False}]
            cards = {os.path.basename(c["path"]): c for c in drives.list_cards(vols)}
            self.assertEqual(cards["sd"]["layouts"], ["Stills camera (DCIM)"])
            self.assertIn("Sony XAVC / cinema (M4ROOT)", cards["cfe"]["layouts"])  # fixed NVMe reader still found
            self.assertIn("RED (RDM/RDC)", cards["red"]["layouts"])
            self.assertFalse(cards["empty"]["camera_card"])
            self.assertNotIn("fixed", cards)  # ordinary fixed disk ignored
            photos, clips, xml = drives.newest_files(red)
            self.assertEqual(len(clips), 1)

    def test_sony_nrt(self):
        xml = ('<?xml version="1.0"?><NonRealTimeMeta xmlns="urn:schemas-professionalDisc:nonRealTimeMeta:ver.2.20">'
               '<Device manufacturer="Sony" modelName="ILME-FX6V" serialNo="5012345"/></NonRealTimeMeta>')
        with tempfile.NamedTemporaryFile("w", suffix="M01.XML", delete=False) as fh:
            fh.write(xml)
        try:
            m = drives.sony_nrt(fh.name)
            self.assertEqual((m["model"], m["serial"]), ("ILME-FX6V", "5012345"))
        finally:
            os.remove(fh.name)


if __name__ == "__main__":
    unittest.main()
