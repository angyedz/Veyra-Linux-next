"""Safety and image format tests for disposable QEMU Glacier QA."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/qemu-glacier-capture.py"
spec = importlib.util.spec_from_file_location("glacier_capture", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class QemuGlacierTests(unittest.TestCase):
    def test_times(self):
        self.assertEqual(module.parse_times("30,80"), [30, 80])
        for invalid in ("", "5", "301", "60,30", "40,40", "10,20,30,40",
                        "bad", "25,"):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                module.parse_times(invalid)

    def test_snapshot_only_and_offline(self):
        args = module.qemu_command(Path("/tmp/guest.raw"),
            Path("/tmp/OVMF_CODE.fd"), Path("/tmp/OVMF_VARS-copy.fd"),
            Path("/tmp/hmp.sock"), Path("/tmp/serial.log"), 4096)
        rendered = " ".join(args)
        self.assertIn("snapshot=on", rendered)
        self.assertIn("readonly=on", rendered)
        self.assertIn("OVMF_VARS-copy.fd", rendered)
        self.assertIn("-nic none", rendered)
        self.assertNotIn("accel=kvm", rendered)
        self.assertIn("-display none", rendered)

    def test_ppm_to_png_exact_decode(self):
        with tempfile.TemporaryDirectory() as temp:
            src, dst = Path(temp) / "sample.ppm", Path(temp) / "sample.png"
            src.write_bytes(b"P6\\n64 64\\n255\\n" + b"\\x11\\x33\\x55" * (64 * 64))
            self.assertEqual(module.ppm_to_png(src, dst), (64, 64))
            self.assertTrue(dst.read_bytes().startswith(bytes.fromhex("89504e470d0a1a0a")))
            src.write_bytes(b"P6\\n64 64\\n255\\n" + b"\\x11" * 10)
            with self.assertRaises(ValueError):
                module.ppm_to_png(src, dst)

    def test_prevent_overwriting_existing_output(self):
        with tempfile.TemporaryDirectory() as temp:
            image = Path(temp) / "disk.raw"
            image.write_bytes(b"dummy")
            report = Path(temp) / "existing"
            report.mkdir()
            with self.assertRaises(SystemExit) as exc:
                module.main(["--image", str(image), "--output", str(report)])
            self.assertEqual(exc.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
