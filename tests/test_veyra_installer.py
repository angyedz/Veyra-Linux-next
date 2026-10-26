from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("veyra_installer", ROOT / "scripts/veyra-install.py")
assert SPEC is not None and SPEC.loader is not None
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallerManifestTests(unittest.TestCase):
    def test_live_installer_uses_the_veyra_package_manifest(self) -> None:
        version, packages = installer.package_manifest(ROOT)

        self.assertEqual(version, "0.1.0")
        self.assertIn("libdnf5", packages)
        self.assertIn("python3-libdnf5", packages)
        self.assertNotIn("dnf5", packages)
        self.assertNotIn("dnf", packages)

    def test_current_disk_layout_size_is_reported_from_repart_definitions(self) -> None:
        expected = 55 * 1024**3 + (512 + 1 + 64) * 1024**2

        self.assertEqual(installer.minimum_disk_size(ROOT / "mkosi.repart"), expected)

    def test_package_manifest_rejects_unsafe_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory) / "mkosi.conf"
            config.write_text("""
[Output]
ImageVersion=0.1.0
[Content]
Packages=systemd;touch /tmp/pwned
""", encoding="utf-8")
            with self.assertRaises(installer.InstallError):
                installer.parse_mkosi_config(config)

    def test_package_manifest_rejects_duplicate_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = pathlib.Path(directory) / "mkosi.conf"
            config.write_text("""
[Output]
ImageVersion=0.1.0
[Content]
Packages=systemd
Packages=systemd
""", encoding="utf-8")
            with self.assertRaises(installer.InstallError):
                installer.parse_mkosi_config(config)

    def test_live_media_device_is_mapped_to_its_whole_disk(self) -> None:
        devices = [{
            "path": "/dev/sdb",
            "name": "sdb",
            "type": "disk",
            "children": [{"path": "/dev/sdb1", "name": "sdb1", "type": "part"}],
        }]

        self.assertEqual(
            installer.live_media_parent(pathlib.Path("/dev/sdb1"), devices),
            "/dev/sdb",
        )


if __name__ == "__main__":
    unittest.main()
