"""Ensure the QEMU boot gate refuses firmware-only and screenshot-only success."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("iso_boot_gate", ROOT / "scripts/verify-live-iso-qemu.py")
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)


def good_report():
    return {
        "cdrom_readonly": True,
        "network_disabled": True,
        "efi_variables_temporary": True,
        "modes": [
            {
                "mode": mode,
                "errors": [],
                "screenshots": [{"file": "screen-300.png", "seconds_after_launch": 300, "non_black_pixels_detected": True}],
                "serial_signals": {
                    "kernel_seen": True,
                    "init_seen": True,
                    "live_desktop_confirmed": False,
                    "plasma_runtime_confirmed": True,
                    "plasma_runtime_failed": False,
                    "vpkg_guest_test_passed": True,
                    "installer_preflight_passed": True,
                    "vpkg_guest_test_failed": False,
                },
            }
            for mode in ("bios", "uefi")
        ],
    }


class FirmwareBootGateTests(unittest.TestCase):
    def test_kernel_and_init_can_pass_without_claiming_desktop(self):
        self.assertEqual(module.verify(good_report()), [])

    def test_installer_read_only_preflight_must_be_proven_on_both_firmwares(self):
        report = good_report()
        report["modes"][0]["serial_signals"]["installer_preflight_passed"] = False
        failures = module.verify(report)
        self.assertTrue(any("installer read-only preflight" in failure for failure in failures))
        report = good_report()
        report["modes"][1]["serial_signals"].pop("installer_preflight_passed")
        failures = module.verify(report)
        self.assertTrue(any("uefi: installer read-only preflight" in failure for failure in failures))

    def test_missing_kernel_or_init_must_fail(self):
        for missing in ("kernel_seen", "init_seen"):
            report = good_report()
            report["modes"][0]["serial_signals"][missing] = False
            with self.subTest(missing=missing):
                self.assertTrue(module.verify(report))

    def test_kde_splash_timeout_or_missing_milou_fails_green_boot(self):
        report = good_report()
        report["modes"][0]["serial_signals"]["ksplash_timed_out"] = True
        report["modes"][1]["serial_signals"]["milou_qml_missing"] = True
        messages = module.verify(report)
        self.assertTrue(any("splash screen timed out" in text for text in messages))
        self.assertTrue(any("plasma-milou" in text for text in messages))

    def test_desktop_process_probe_is_required(self):
        report = good_report()
        report["modes"][0]["serial_signals"]["plasma_runtime_confirmed"] = False
        report["modes"][1]["serial_signals"]["plasma_runtime_failed"] = True
        failures = module.verify(report)
        self.assertTrue(any("plasmashell" in failure for failure in failures))
        self.assertTrue(any("process probe" in failure for failure in failures))

    def test_fully_black_final_screenshot_must_fail(self):
        report = good_report()
        report["modes"][0]["screenshots"][-1]["non_black_pixels_detected"] = False
        failures = module.verify(report)
        self.assertTrue(any("visible framebuffer" in failure for failure in failures))

    def test_missing_or_malformed_screenshot_visibility_is_not_green(self):
        for missing in (None, "", 0):
            report = good_report()
            frame = report["modes"][0]["screenshots"][-1]
            frame.pop("non_black_pixels_detected")
            if missing is not None:
                frame["non_black_pixels_detected"] = missing
            with self.subTest(evidence=missing):
                self.assertTrue(any("visible framebuffer" in text for text in module.verify(report)))
        report = good_report()
        report["modes"][0]["screenshots"] = {"last_frame": "screen-300.png"}
        self.assertTrue(any("malformed" in text for text in module.verify(report)))

    def test_early_boot_splash_does_not_count_as_a_desktop(self):
        for seconds in (0, 60, 180, 299, None):
            report = good_report()
            frame = report["modes"][0]["screenshots"][-1]
            if seconds is None:
                frame.pop("seconds_after_launch")
            else:
                frame["seconds_after_launch"] = seconds
            with self.subTest(seconds=seconds):
                self.assertTrue(any("too early" in failure for failure in module.verify(report)))

    def test_qemu_failure_or_unchecked_firmware_fails(self):
        report = good_report()
        report["modes"][1]["errors"].append("QEMU exited 1")
        self.assertTrue(module.verify(report))
        report = good_report()
        report["modes"].pop()
        self.assertTrue(module.verify(report))

    def test_plasma_crash_or_pam_failure_must_fail(self):
        report = good_report()
        report["modes"][0]["serial_signals"]["plasma_session_crashed"] = True
        report["modes"][1]["serial_signals"]["pam_systemd_missing"] = True
        failures = module.verify(report)
        self.assertTrue(any("crashed Plasma" in failure for failure in failures))
        self.assertTrue(any("systemd-pam" in failure for failure in failures))

    def test_missing_images_and_unsafe_configuration_fail(self):
        report = good_report()
        report["modes"][0]["screenshots"] = []
        report["network_disabled"] = False
        self.assertGreaterEqual(len(module.verify(report)), 2)


if __name__ == "__main__":
    unittest.main()
