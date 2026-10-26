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

    def test_live_iso_has_both_firmware_bootloaders(self) -> None:
        kickstart = (ROOT / "installer/veyra-live.ks.in").read_text()
        packages = kickstart.split("%packages", 1)[1].split("%end", 1)[0]
        names = {line.partition("#")[0].strip() for line in packages.splitlines()}
        for pkg in ("grub2-efi-x64-cdboot", "grub2-efi-x64", "grub2-pc",
                    "grub2-pc-modules", "dracut-config-generic", "dracut-live"):
            with self.subTest(package=pkg):
                self.assertIn(pkg, names)
        self.assertIn("/boot/efi/EFI/fedora/gcdx64.efi", kickstart)
        self.assertIn('rpm --root="$live_root" -q calamares', kickstart)

    def test_live_iso_keeps_installer_dracut_directory(self) -> None:
        kickstart = (ROOT / "installer/veyra-live.ks.in").read_text()
        self.assertIn('"$live_root/usr/share/veyra/installer/source/installer"', kickstart)
        self.assertIn('"$source_root/installer/dracut"', kickstart)

    def test_live_installer_bundles_recovery_at_composer_lookup_path(self) -> None:
        kickstart = (ROOT / "installer/veyra-live.ks.in").read_text()
        self.assertIn(
            '"$live_root/usr/share/veyra/installer/source/assets/veyra-recovery.efi"',
            kickstart,
        )
        self.assertNotIn(
            '"$live_root/usr/share/veyra/installer/assets/veyra-recovery.efi"',
            kickstart,
        )

    def test_recovery_uki_copy_into_target_esp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            source = base / "source"
            (source / "assets").mkdir(parents=True)
            (source / "assets/veyra-recovery.efi").write_bytes(b"isolated prototype fixture")
            esp = base / "esp"
            installer.write_recovery_uki(esp, source)
            self.assertEqual(
                (esp / "EFI/Linux/veyra-recovery.efi").read_bytes(),
                b"isolated prototype fixture",
            )

    def test_recovery_uki_must_not_be_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            with self.assertRaisesRegex(installer.InstallError, "Recovery UKI"):
                installer.write_recovery_uki(base / "esp", base / "source")

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
