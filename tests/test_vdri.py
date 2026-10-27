from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "mkosi.extra/usr/bin/v-dri"
SPEC = importlib.util.spec_from_loader("veyra_vdri", SourceFileLoader("veyra_vdri", str(SCRIPT)))
assert SPEC and SPEC.loader
vdri = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vdri)


class VdriQemuSerialTests(unittest.TestCase):
    def test_firmware_invalid_utf8_cannot_hide_a_pass_marker(self) -> None:
        # GRUB/firmware mixes terminal byte sequences into serial output.
        import importlib.util
        from importlib.machinery import SourceFileLoader
        script = Path(__file__).resolve().parents[1] / "scripts/test-vdri-qemu.py"
        spec = importlib.util.spec_from_loader("veyra_vdri_qemu", SourceFileLoader("veyra_vdri_qemu", str(script)))
        assert spec is not None and spec.loader is not None
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        decoded = helper.decode_qemu_output(
            b"EFI\\xff\\xfe\\nVDRI_QEMU_PASS\\n".replace(b"\\xff", b"\xff").replace(b"\\xfe", b"\xfe"),
            b"BOOT: ok",
        )
        self.assertIn("VDRI_QEMU_PASS", decoded)
        self.assertIn("\ufffd", decoded)


class VdriStatusTests(unittest.TestCase):
    def make_device(
        self,
        root: Path,
        *,
        taint: str = "",
        driver: str | None = "virtio_gpu",
        transport: str = "virtio-pci",
    ) -> None:
        signature_parameter = root / "module/module/parameters/sig_enforce"
        signature_parameter.parent.mkdir(parents=True)
        signature_parameter.write_text("Y\n", encoding="ascii")
        device = root / "devices/pci0000:00/0000:00:02.0"
        device.mkdir(parents=True)
        (device / "vendor").write_text("0x1af4\n", encoding="ascii")
        (device / "device").write_text("0x1050\n", encoding="ascii")
        (device / "class").write_text("0x030000\n", encoding="ascii")
        card = root / "class/drm/card0"
        card.mkdir(parents=True)
        (card / "device").symlink_to("../../../devices/pci0000:00/0000:00:02.0")

        def add_driver(driver_name: str, bus: str, link: Path, *, module: bool = True) -> None:
            driver_path = root / "bus" / bus / "drivers" / driver_name
            driver_path.mkdir(parents=True)
            if module:
                module_path = root / "module" / driver_name
                module_path.mkdir(parents=True)
                (module_path / "taint").write_text(taint, encoding="ascii")
                (driver_path / "module").symlink_to(module_path)
            link.symlink_to(driver_path)

        if transport == "virtio-pci":
            add_driver(transport, "pci", device / "driver", module=False)
            if driver is not None:
                virtio_device = device / "virtio0"
                virtio_device.mkdir()
                add_driver(driver, "virtio", virtio_device / "driver")
        elif driver is not None:
            add_driver(driver, "pci", device / "driver")

    def test_reports_bound_kernel_driver_and_hardware_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_device(root)

            result = vdri.collect_status(root, kernel="6.15.0-veyra")

        self.assertEqual(result["schema"], 2)
        self.assertEqual(result["kernel"], "6.15.0-veyra")
        self.assertEqual(result["driver_policy"], "fedora-in-tree-only")
        self.assertEqual(result["module_signature_enforcement"], "enforced")
        self.assertEqual(result["devices"], [{
            "name": "card0",
            "vendor_id": "1af4",
            "device_id": "1050",
            "driver": "virtio_gpu",
            "module": "virtio_gpu",
            "module_taint": "",
            "status": "bound",
        }])

    def test_reports_graphics_function_behind_virtio_pci_transport(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_device(root)

            device = root / "devices/pci0000:00/0000:00:02.0"
            self.assertEqual((device / "driver").resolve().name, "virtio-pci")
            self.assertEqual((device / "virtio0/driver").resolve().name, "virtio_gpu")
            result = vdri.collect_status(root)

        self.assertEqual(result["devices"][0]["driver"], "virtio_gpu")
        self.assertEqual(result["devices"][0]["module"], "virtio_gpu")

    def test_reports_direct_pci_graphics_driver(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_device(root, driver="amdgpu", transport="pci")

            result = vdri.collect_status(root)

        self.assertEqual(result["devices"][0]["driver"], "amdgpu")
        self.assertEqual(result["devices"][0]["module"], "amdgpu")

    def test_marks_externally_tainted_driver_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_device(root, taint="O")

            result = vdri.collect_status(root)

        self.assertEqual(result["devices"][0]["status"], "external-module-detected")

    def test_reports_permissive_module_signature_policy_as_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_device(root)
            (root / "module/module/parameters/sig_enforce").write_text("N\n", encoding="ascii")

            result = vdri.collect_status(root)

        self.assertEqual(result["module_signature_enforcement"], "permissive")
        self.assertTrue(vdri._has_problem(result))

    def test_reports_unavailable_module_signature_state_as_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_device(root)
            (root / "module/module/parameters/sig_enforce").unlink()

            result = vdri.collect_status(root)

        self.assertEqual(result["module_signature_enforcement"], "unavailable")
        self.assertTrue(vdri._has_problem(result))

    def test_marks_device_without_bound_driver_unbound(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_device(root, driver=None)

            result = vdri.collect_status(root)

        self.assertEqual(result["devices"][0]["status"], "unbound")
        self.assertIsNone(result["devices"][0]["module"])

    def test_does_not_treat_unbound_virtio_gpu_transport_as_graphics_driver(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_device(root, driver=None)

            result = vdri.collect_status(root)

        self.assertIsNone(result["devices"][0]["driver"])
        self.assertEqual(result["devices"][0]["status"], "unbound")

    def test_ignores_drm_connector_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_device(root)
            (root / "class/drm/card0-DP-1").mkdir()
            (root / "class/drm/renderD128").mkdir()

            result = vdri.collect_status(root)

        self.assertEqual([item["name"] for item in result["devices"]], ["card0"])

    def test_reports_no_graphics_devices(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "class/drm").mkdir(parents=True)
            result = vdri.collect_status(root, kernel="6.15.0-veyra")

        self.assertEqual(result, {
            "schema": 2,
            "kernel": "6.15.0-veyra",
            "driver_policy": "fedora-in-tree-only",
            "module_signature_enforcement": "unavailable",
            "devices": [],
        })

    def test_rejects_device_symlink_escape_from_sysfs_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "sys"
            root.mkdir()
            outside = Path(temporary) / "outside-device"
            outside.mkdir()
            card = root / "class/drm/card0"
            card.mkdir(parents=True)
            (card / "device").symlink_to(outside)

            with self.assertRaisesRegex(vdri.VdriError, "escapes the sysfs root"):
                vdri.collect_status(root)

    def test_cli_json_status_reports_problem_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_device(root, taint="O")
            output = []
            with patch.object(vdri, "SYSFS_ROOT", root), patch("sys.stdout.write", side_effect=output.append):
                result = vdri.main(["status", "--json"])

        self.assertEqual(result, 2)
        self.assertEqual(json.loads("".join(output))["devices"][0]["status"], "external-module-detected")


if __name__ == "__main__":
    unittest.main()
