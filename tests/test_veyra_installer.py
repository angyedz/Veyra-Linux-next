from __future__ import annotations

import importlib.util
import pathlib
import struct
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("veyra_installer", ROOT / "scripts/veyra-install.py")
assert SPEC is not None and SPEC.loader is not None
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)



def write_recovery_fixture(path: pathlib.Path, *, image_id: str = "veyra-recovery") -> bytes:
    """Minimal PE layout for metadata tests; never bootable or trusted."""
    image = bytearray(0x400 + 4 * 512)
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3c, 0x80)
    image[0x80:0x84] = b"PE\x00\x00"
    struct.pack_into("<H", image, 0x86, 4)
    struct.pack_into("<H", image, 0x94, 0xf0)
    table = 0x80 + 24 + 0xf0
    payloads = (
        (b".osrel", (
            f"ID=fedora\nVERSION_ID=44\nIMAGE_ID={image_id}\nIMAGE_VERSION=0.1.0\n"
        ).encode("ascii")),
        (b".cmdline", b"rdinit=/init console=ttyS0\x00"),
        (b".linux", b"synthetic kernel fixture"),
        (b".initrd", b"synthetic initrd fixture"),
    )
    for index, (name, payload) in enumerate(payloads):
        header = table + index * 40
        image[header:header + len(name)] = name
        offset = 0x400 + index * 512
        struct.pack_into("<II", image, header + 16, 512, offset)
        image[offset:offset + len(payload)] = payload
    path.write_bytes(image)
    return bytes(image)


class InstallerManifestTests(unittest.TestCase):
    def test_repositories_are_refreshed_before_destructive_repart(self) -> None:
        from unittest import mock
        import subprocess

        with mock.patch.object(installer.subprocess, "run") as run:
            installer.verify_package_repository_access()
        run.assert_called_once_with(
            ["dnf5", "--releasever=44", "--refresh", "makecache"],
            check=True, timeout=180,
        )

        source = (ROOT / "scripts/veyra-install.py").read_text()
        action = source.split("def install_system(", 1)[1].split("\ndef main()", 1)[0]
        self.assertLess(
            action.index("verify_package_repository_access()"),
            action.index('"systemd-repart", "--dry-run=no", "--empty=force"'),
        )

    def test_repository_failure_aborts_before_erasing_disk(self) -> None:
        from unittest import mock
        import subprocess
        with mock.patch.object(installer.subprocess, "run", side_effect=subprocess.TimeoutExpired("dnf5", 180)):
            with self.assertRaisesRegex(installer.InstallError, "NOT changed"):
                installer.verify_package_repository_access()
        with mock.patch.object(installer.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "dnf5")):
            with self.assertRaisesRegex(installer.InstallError, "NOT changed"):
                installer.verify_package_repository_access()

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

    def test_installed_user_is_created_without_inheriting_live_passwords(self) -> None:
        import unittest.mock as mock

        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(installer, "command") as command, mock.patch.object(
                installer.subprocess, "run"
            ) as run:
                installer.configure_installed_accounts(
                    pathlib.Path(directory), "tester", "user secret", "root secret"
                )
            command.assert_called_once()
            self.assertIn("wheel", command.call_args.args[0])
            args, kwargs = run.call_args
            self.assertEqual(args[0][-1], "/usr/sbin/chpasswd")
            self.assertEqual(kwargs["input"], "tester:user secret\nroot:root secret\n")
            self.assertEqual(kwargs["check"], True)

    def test_installed_account_input_rejects_invalid_users_before_erasure(self) -> None:
        import unittest.mock as mock

        with mock.patch("builtins.input", return_value="root"):
            with self.assertRaises(installer.InstallError):
                installer.requested_account()

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

    def test_live_vm_policy_requires_installer_preflight_before_pass(self) -> None:
        vm_check = (ROOT / "scripts/veyra-live-vpkg-vm-check.sh").read_text(encoding="utf-8")
        self.assertIn("step installer-preflight", vm_check)
        self.assertIn("/usr/libexec/veyra-installer --preflight", vm_check)
        self.assertEqual(vm_check.count("/usr/bin/runuser -u liveuser -- /usr/bin/sudo -n /usr/libexec/veyra-installer --preflight"), 2)
        self.assertIn("VEYRA_LIVE_INSTALLER_PREFLIGHT_PASS recovery=present", vm_check)
        self.assertIn("VEYRA_LIVE_INSTALLER_PREFLIGHT_PASS recovery=absent-rejected", vm_check)
        self.assertIn("Live installer accepted installation without a Recovery UKI", vm_check)
        self.assertIn("assets/veyra-recovery[.]efi|Recovery UKI", vm_check)
        self.assertLess(vm_check.index("step installer-preflight"),
                        vm_check.index("VEYRA_LIVE_VPKG_CI_PASS"))
        # The VM must never run the destructive installer without --preflight.
        self.assertNotIn("/usr/libexec/veyra-installer --target", vm_check)
        self.assertNotIn("systemd-repart", vm_check)

    def test_calamares_welcome_allows_unprivileged_live_frontend(self) -> None:
        welcome = (ROOT / "installer/calamares/modules/welcome.conf").read_text(encoding="utf-8")
        requirements = welcome.split("requirements:", 1)[1].split("geoip:", 1)[0]
        # Calamares is a normal Wayland client. Root is only acquired by the
        # preflighted, restricted backend, so root must not be a UI gate.
        self.assertNotRegex(requirements, r"(?m)^\s*-\s*root\s*$")
        self.assertRegex(requirements, r"(?m)^\s*-\s*internet\s*$")
        builder = (ROOT / "scripts/build-native-live-iso.sh").read_text(encoding="utf-8")
        self.assertIn('if [[ "${EUID}" -eq 0 ]]; then', builder)
        self.assertIn("/usr/bin/sudo -n /usr/libexec/veyra-installer --preflight", builder)

    def test_lorax_calamares_overridden_data_root_resolves_qml(self) -> None:
        kickstart = (ROOT / "installer/veyra-live.ks.in").read_text(encoding="utf-8")
        self.assertIn('test -d "$live_root/usr/share/calamares/qml"', kickstart)
        self.assertIn('ln -s ../../usr/share/calamares/qml "$live_root/etc/calamares/qml"', kickstart)
        self.assertIn('test -d "$live_root/etc/calamares/qml"', kickstart)
        self.assertIn('Calamares QML package resources are missing.', kickstart)
        self.assertNotIn('mkdir -p "$live_root/etc/calamares/qml"', kickstart)

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

    def test_boot_assets_survive_disposable_staging_disk_reformat(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = pathlib.Path(directory)
            root = work / "scratch-root"
            kernel = root / "usr/lib/modules/7.2.13-200.fc44.x86_64/vmlinuz"
            initrd = root / "boot/initramfs-7.2.13-200.fc44.x86_64.img"
            release = root / "usr/lib/os-release"
            for source, content in ((kernel, b"kernel"), (initrd, b"initrd"),
                                    (release, b"ID=fedora\\nVERSION_ID=44\\n")):
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(content)
            host_assets = work / "host-private"
            host_assets.mkdir(mode=0o700)
            staged_kernel, staged_initrd, staged_release = installer.stage_installer_boot_assets(
                root, kernel, initrd, host_assets
            )
            with self.assertRaisesRegex(installer.InstallError, "overwrite staged"):
                installer.stage_installer_boot_assets(root, kernel, initrd, host_assets)
            # The scratch disk is unmounted/reformatted at this point.
            import shutil
            shutil.rmtree(root)
            self.assertEqual(staged_kernel.read_bytes(), b"kernel")
            self.assertEqual(staged_initrd.read_bytes(), b"initrd")
            self.assertEqual(staged_release.read_bytes(), b"ID=fedora\\nVERSION_ID=44\\n")

    def test_boot_assets_reject_missing_initrd_and_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = pathlib.Path(directory)
            root = work / "scratch-root"
            kernel = root / "usr/lib/modules/test/vmlinuz"
            initrd = root / "boot/initramfs-test.img"
            release = root / "usr/lib/os-release"
            for path in (kernel, release):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"valid")
            dest = work / "staged"
            dest.mkdir()
            with self.assertRaisesRegex(installer.InstallError, "missing or invalid"):
                installer.stage_installer_boot_assets(root, kernel, initrd, dest)
            kernel.unlink()
            kernel.symlink_to(release)
            with self.assertRaisesRegex(installer.InstallError, "missing or invalid"):
                installer.stage_installer_boot_assets(root, kernel, initrd, dest)

    def test_installer_stages_boot_files_before_reformat_then_uses_copies(self) -> None:
        source = (ROOT / "scripts/veyra-install.py").read_text(encoding="utf-8")
        installed = source.split("def install_system(", 1)[1].split("\ndef main()", 1)[0]
        stage_call = installed.index("stage_installer_boot_assets(")
        scratch_unmount = installed.index('command(["umount", str(SCRATCH_MOUNT)])')
        ext4_format = installed.index('command(["mkfs.ext4"')
        uki = installed.index('"ukify", "build"')
        self.assertLess(stage_call, scratch_unmount)
        self.assertLess(scratch_unmount, ext4_format)
        self.assertLess(ext4_format, uki)
        self.assertIn('f"--os-release=@{os_release}"', installed)
        self.assertIn("shutil.copy2(linux, bios_dir", installed)
        self.assertIn("shutil.copy2(initrd, bios_dir", installed)
        self.assertIn("shutil.rmtree(staged_boot_dir, ignore_errors=True)", installed)

    def test_recovery_uki_copy_into_target_esp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            source = base / "source"
            (source / "assets").mkdir(parents=True)
            payload = write_recovery_fixture(source / "assets/veyra-recovery.efi")
            esp = base / "esp"
            installer.write_recovery_uki(esp, source)
            self.assertEqual(
                (esp / "EFI/Linux/veyra-recovery.efi").read_bytes(),
                payload,
            )

    def test_recovery_preflight_rejects_arbitrary_nonempty_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            src = root / "source"
            (src / "assets").mkdir(parents=True)
            recovery = src / "assets/veyra-recovery.efi"
            recovery.write_bytes(b"not a PE binary" * 100)
            with self.assertRaisesRegex(installer.InstallError, "Recovery UKI"):
                installer.validate_recovery_uki_metadata(recovery)
            with self.assertRaisesRegex(installer.InstallError, "Recovery UKI"):
                installer.write_recovery_uki(root / "esp", src)
            self.assertFalse((root / "esp/EFI/Linux/veyra-recovery.efi").exists())

    def test_recovery_preflight_enforces_pe_identity_and_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recovery = pathlib.Path(directory) / "veyra-recovery.efi"
            write_recovery_fixture(recovery)
            installer.validate_recovery_uki_metadata(recovery)

            write_recovery_fixture(recovery, image_id="veyra-next")
            with self.assertRaisesRegex(installer.InstallError, "IMAGE_ID"):
                installer.validate_recovery_uki_metadata(recovery)

            image = bytearray(write_recovery_fixture(recovery))
            table = 0x80 + 24 + 0xf0
            image[table + 3 * 40:table + 3 * 40 + 8] = b".other\x00\x00"
            recovery.write_bytes(image)
            with self.assertRaisesRegex(installer.InstallError, "missing .initrd"):
                installer.validate_recovery_uki_metadata(recovery)

            image = bytearray(write_recovery_fixture(recovery))
            struct.pack_into("<I", image, table + 3 * 40 + 20, len(image) + 1024)
            recovery.write_bytes(image)
            with self.assertRaisesRegex(installer.InstallError, "bounds"):
                installer.validate_recovery_uki_metadata(recovery)

            image = write_recovery_fixture(recovery)
            recovery.write_bytes(image[:700])
            with self.assertRaisesRegex(installer.InstallError, "bounds"):
                installer.validate_recovery_uki_metadata(recovery)

    def test_recovery_uki_must_not_be_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = pathlib.Path(directory)
            with self.assertRaisesRegex(installer.InstallError, "Recovery UKI"):
                installer.write_recovery_uki(base / "esp", base / "source")


    def test_preflight_checks_complete_source_and_requires_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = pathlib.Path(directory)
            for rel in (
                "mkosi.conf",
                "mkosi.extra/usr/lib/veyra/grub_ota.py",
                "installer/dracut/99veyra-sysroot/module-setup.sh",
                "assets/veyra-recovery.efi",
                "mkosi.repart/11-root.conf",
            ):
                entry = source / rel
                entry.parent.mkdir(parents=True, exist_ok=True)
                entry.write_text("safe test fixture", encoding="utf-8")
            write_recovery_fixture(source / "assets/veyra-recovery.efi")
            installer.validate_installer_assets(source)
            recovery = source / "assets/veyra-recovery.efi"
            recovery.unlink()
            with self.assertRaisesRegex(installer.InstallError, "assets/veyra-recovery"):
                installer.validate_installer_assets(source)
            external = source / "outside"
            external.write_text("external", encoding="utf-8")
            recovery.symlink_to(external)
            with self.assertRaisesRegex(installer.InstallError, "assets/veyra-recovery"):
                installer.validate_installer_assets(source)

    def test_rejects_target_identity_change(self) -> None:
        sample = {"path": "/dev/vda", "size": 64 * 1024**3, "model": "QEMU",
                  "maj:min": "253:0", "serial": "disk-1", "wwn": ""}
        installer.assert_target_identity_unchanged(sample, sample.copy())
        for key, changed in (("path", "/dev/vdb"), ("size", 32 * 1024**3),
                             ("model", "NEW"), ("maj:min", "253:1"),
                             ("serial", "disk-2"), ("wwn", "different")):
            with self.subTest(key=key), self.assertRaisesRegex(
                installer.InstallError, "identity changed"
            ):
                installer.assert_target_identity_unchanged(
                    sample, {**sample, key: changed}
                )

    def test_rejects_symlinked_recovery_payload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            src = root / "source"
            (src / "assets").mkdir(parents=True)
            external = root / "outside.efi"
            external.write_bytes(b"arbitrary")
            (src / "assets/veyra-recovery.efi").symlink_to(external)
            with self.assertRaisesRegex(installer.InstallError, "Recovery UKI"):
                installer.write_recovery_uki(root / "esp", src)

    def test_installer_exposes_nondestructive_preflight(self) -> None:
        source = (ROOT / "scripts/veyra-install.py").read_text()
        self.assertIn('parser.add_argument("--preflight"', source)
        self.assertIn('print("Preflight only: no disk changes were made.")', source)
        self.assertLess(source.index("validate_installer_assets(source)"),
                        source.index('"systemd-repart", "--dry-run=no"'))
        self.assertLess(source.index("assert_target_identity_unchanged(candidate, refreshed)"),
                        source.index('"systemd-repart", "--dry-run=no"'))

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
