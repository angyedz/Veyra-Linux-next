"""Offline tests for the Legacy BIOS/UEFI ISO QEMU diagnostic harness."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("iso_qemu", ROOT / "scripts/test-live-iso-qemu.py")
assert SPEC and SPEC.loader
iso_qemu = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(iso_qemu)


class IsoQemuSmokeTests(unittest.TestCase):
    def test_times(self):
        self.assertEqual(iso_qemu.parse_capture_times("20,60"), [20, 60])
        for invalid in ("", "5", "301", "40,30", "40,40", "10,20,30,40", "a", "40,"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                iso_qemu.parse_capture_times(invalid)

    def test_bios_readonly_optical_only(self):
        args = iso_qemu.qemu_args(
            Path("/tmp/disk.iso"), "bios", Path("/tmp/mon.sock"),
            Path("/tmp/serial.log"), 3072,
        )
        rendered = " ".join(args)
        self.assertIn("media=cdrom", rendered)
        self.assertIn("readonly=on", rendered)
        self.assertIn("-nic none", rendered)
        self.assertNotIn("pflash", rendered)
        self.assertNotIn("accel=kvm", rendered)
        self.assertIn("-boot order=d,menu=off", rendered)
        self.assertIn("-vga none -device virtio-vga", rendered)
        self.assertNotIn("-vga virtio", rendered)

    def test_uefi_uses_disposable_varstore(self):
        args = iso_qemu.qemu_args(
            Path("/tmp/disk.iso"), "uefi", Path("/tmp/mon.sock"),
            Path("/tmp/serial.log"), 3072,
            Path("/tmp/CODE.fd"), Path("/tmp/copied-VARS.fd"),
        )
        rendered = " ".join(args)
        self.assertIn("unit=0,readonly=on", rendered)
        self.assertIn("unit=1,file=/tmp/copied-VARS.fd", rendered)
        self.assertNotIn("if=virtio", rendered)

    def test_ppm_to_png_and_first_pixel_whitespace(self):
        with tempfile.TemporaryDirectory() as directory:
            source, result = Path(directory) / "sample.ppm", Path(directory) / "result.png"
            # Pixel 0 begins with an ASCII newline; it must remain pixel data.
            pixels = b"\n\r " + b"\x13\x34\x56" * (64 * 64 - 1)
            source.write_bytes(b"P6\n64 64\n255\n" + pixels)
            self.assertEqual(iso_qemu.ppm_to_png(source, result), (64, 64))
            png = result.read_bytes()
            self.assertTrue(png.startswith(bytes.fromhex("89504e470d0a1a0a")))
            offset = 8
            inflated = bytearray()
            while offset < len(png):
                size = struct.unpack(">I", png[offset:offset + 4])[0]
                kind = png[offset + 4:offset + 8]
                body = png[offset + 8:offset + 8 + size]
                crc = struct.unpack(">I", png[offset + 8 + size:offset + 12 + size])[0]
                self.assertEqual(zlib.crc32(kind + body), crc)
                if kind == b"IDAT":
                    inflated.extend(zlib.decompress(body))
                offset += 12 + size
            self.assertEqual(inflated[0], 0)
            self.assertEqual(inflated[1:4], b"\n\r ")
            source.write_bytes(b"P6\n64 64\n255\n" + b"\x01" * 9)
            with self.assertRaises(iso_qemu.SmokeError):
                iso_qemu.ppm_to_png(source, result)

    def test_serial_markers_not_fake_success(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "serial.log"
            path.write_text("Linux version 6.0\nsystemd[1]: Started target", encoding="utf-8")
            outcome = iso_qemu.serial_signals(path)
            self.assertTrue(outcome["kernel_seen"])
            self.assertTrue(outcome["init_seen"])
            self.assertFalse(outcome["live_desktop_confirmed"])

    def test_output_refuses_reuse_before_invoking_qemu(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "boot.iso"
            image.write_bytes(b"fixture")
            output = Path(directory) / "existing-report"
            output.mkdir()
            with self.assertRaises(SystemExit) as ctx:
                iso_qemu.main(["--iso", str(image), "--output", str(output), "--modes", "bios"])
            self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
