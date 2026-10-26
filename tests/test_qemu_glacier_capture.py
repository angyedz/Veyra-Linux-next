"""Safety and image format tests for disposable QEMU Glacier QA."""
import importlib.util
import json
import socket
import threading
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
        self.assertIn("-qmp", args)
        self.assertNotIn("-monitor", args)

    def test_ppm_to_png_exact_decode(self):
        with tempfile.TemporaryDirectory() as temp:
            src, dst = Path(temp) / "sample.ppm", Path(temp) / "sample.png"
            src.write_bytes(b"P6" + bytes((10,)) + b"64 64" + bytes((10,)) + b"255" + bytes((10,)) + bytes((0x11, 0x33, 0x55)) * (64 * 64))
            self.assertEqual(module.ppm_to_png(src, dst), (64, 64))
            self.assertTrue(dst.read_bytes().startswith(bytes.fromhex("89504e470d0a1a0a")))
            src.write_bytes(b"P6" + bytes((10,)) + b"64 64" + bytes((10,)) + b"255" + bytes((10,)) + bytes((0x11,)) * 10)
            with self.assertRaises(ValueError):
                module.ppm_to_png(src, dst)

    def test_qmp_handshake_and_acknowledged_screenshot(self):
        with tempfile.TemporaryDirectory() as temp:
            monitor = Path(temp) / "qmp.sock"
            screenshot = Path(temp) / "captured.ppm"
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(monitor))
            listener.listen(1)
            observed = []
            server_errors = []

            def fake_qemu():
                try:
                    conn, _ = listener.accept()
                    with conn, conn.makefile("rb") as incoming:
                        conn.sendall((json.dumps({"QMP": {"version": {}, "capabilities": []}}) + "\r\n").encode())
                        for expected in ("qmp_capabilities", "screendump"):
                            message = json.loads(incoming.readline())
                            observed.append(message)
                            assert message["execute"] == expected
                            if expected == "screendump":
                                Path(message["arguments"]["filename"]).write_bytes(b"P6\n64 64\n255\n" + bytes((14, 28, 42)) * 4096)
                            conn.sendall((json.dumps({"return": {}, "id": message["id"]}) + "\r\n").encode())
                except Exception as exc:
                    server_errors.append(exc)
                finally:
                    listener.close()

            worker = threading.Thread(target=fake_qemu, daemon=True)
            worker.start()
            module.screendump(monitor, screenshot, timeout_seconds=5)
            worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            self.assertFalse(server_errors)
            self.assertEqual([x["execute"] for x in observed],
                             ["qmp_capabilities", "screendump"])
            self.assertTrue(screenshot.exists())

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
