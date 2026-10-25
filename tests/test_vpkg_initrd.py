from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import call, patch


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "mkosi.images/initrd/mkosi.extra/usr/libexec/veyra-vpkg-initrd.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_vpkg_initrd", SourceFileLoader("veyra_vpkg_initrd", str(HELPER))
)
assert SPEC and SPEC.loader
initrd = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(initrd)


class VpkgInitrdGenerationTests(unittest.TestCase):
    @staticmethod
    def make_uki(path: Path, os_release: bytes) -> None:
        sections = {b".osrel": (os_release, 0x200)}
        image = bytearray(0x500)
        pe_offset = 0x80
        image[:2] = b"MZ"
        struct.pack_into("<I", image, 0x3C, pe_offset)
        image[pe_offset : pe_offset + 4] = b"PE\0\0"
        struct.pack_into("<H", image, pe_offset + 6, len(sections))
        struct.pack_into("<H", image, pe_offset + 20, 0xF0)
        section_table = pe_offset + 24 + 0xF0
        for index, (name, (content, raw_offset)) in enumerate(sections.items()):
            header = section_table + index * 40
            image[header : header + len(name)] = name
            struct.pack_into("<I", image, header + 8, len(content))
            struct.pack_into("<I", image, header + 16, 0x100)
            struct.pack_into("<I", image, header + 20, raw_offset)
            image[raw_offset : raw_offset + len(content)] = content
        path.write_bytes(image)

    def test_identity_uses_roothash_and_uki_osrel_image_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            uki = Path(temporary) / "boot.efi"
            self.make_uki(uki, b'ID=fedora\nVERSION_ID=44\nIMAGE_VERSION="0.1.7-test"\n')
            identity = initrd.deployment_identity(
                "quiet roothash=" + "a" * 64 + " systemd.unit=graphical.target", uki
            )
        self.assertEqual(identity, ("a" * 64, "0.1.7-test"))

    def test_identity_rejects_missing_duplicate_or_unsupported_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            uki = Path(temporary) / "boot.efi"
            self.make_uki(uki, b'ID=fedora\nVERSION_ID=44\nIMAGE_VERSION="0.1.7"\n')
            for command_line in (
                "quiet",
                "roothash=" + "a" * 64 + " roothash=" + "b" * 64,
                "roothash=invalid",
            ):
                with self.subTest(command_line=command_line), self.assertRaises(initrd.InitrdError):
                    initrd.deployment_identity(command_line, uki)
            self.make_uki(uki, b'ID=fedora\nVERSION_ID=46\nIMAGE_VERSION="0.1.7"\n')
            with self.assertRaisesRegex(initrd.InitrdError, "Fedora 44"):
                initrd.deployment_identity("roothash=" + "a" * 64, uki)

    def test_bootstrap_creates_a_generation_owned_rpm_database_and_active_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sysroot = Path(temporary) / "sysroot"
            rpmdb = sysroot / "usr/lib/sysimage/rpm"
            rpmdb.mkdir(parents=True)
            (rpmdb / "Packages.db").write_bytes(b"base rpm db")
            (sysroot / "etc").mkdir()
            (sysroot / "var").mkdir()

            generation = initrd.ensure_base_generation(sysroot, "a" * 64, "0.1.7")

            self.assertEqual(
                (generation / "upper/usr/lib/sysimage/rpm/Packages.db").read_bytes(),
                b"base rpm db",
            )
            self.assertEqual((rpmdb / "Packages.db").read_bytes(), b"base rpm db")
            self.assertEqual(
                (generation / "manifest.json").read_text(encoding="utf-8"),
                json.dumps(
                    json.loads((generation / "manifest.json").read_text(encoding="utf-8")),
                    sort_keys=True,
                    separators=(",", ":"),
                ) + "\n",
            )
            self.assertEqual((generation.parent / "active").resolve(), generation)
            self.assertTrue((generation / "upper/etc").is_dir())
            self.assertTrue((generation / "work/usr").is_dir())
            self.assertTrue((generation / "work/etc").is_dir())

    def test_bootstrap_fails_closed_for_symlinked_base_rpm_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sysroot = Path(temporary) / "sysroot"
            (sysroot / "usr/lib/sysimage").mkdir(parents=True)
            (sysroot / "var/lib/rpm").mkdir(parents=True)
            (sysroot / "usr/lib/sysimage/rpm").symlink_to("/var/lib/rpm")
            with self.assertRaisesRegex(initrd.InitrdError, "RPM database"):
                initrd.ensure_base_generation(sysroot, "a" * 64, "0.1.7")

    def test_bootstrap_rejects_symlinked_state_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sysroot = root / "sysroot"
            rpmdb = sysroot / "usr/lib/sysimage/rpm"
            rpmdb.mkdir(parents=True)
            (rpmdb / "Packages.db").write_bytes(b"base rpm db")
            (sysroot / "etc").mkdir()
            (sysroot / "var").mkdir()
            outside = root / "untrusted-state"
            outside.mkdir()
            (sysroot / "var/lib").mkdir()
            (sysroot / "var/lib/veyra").symlink_to(outside)

            with self.assertRaisesRegex(initrd.InitrdError, "symlink"):
                initrd.ensure_base_generation(sysroot, "a" * 64, "0.1.7")

    def test_trial_selection_does_not_change_active_until_complete_and_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sysroot = Path(temporary) / "sysroot"
            rpmdb = sysroot / "usr/lib/sysimage/rpm"
            rpmdb.mkdir(parents=True)
            (rpmdb / "Packages.db").write_bytes(b"base rpm db")
            (sysroot / "etc").mkdir()
            (sysroot / "var").mkdir()
            parent = initrd.ensure_base_generation(sysroot, "a" * 64, "0.1.7")

            trial = initrd.create_trial_generation(sysroot, "a" * 64, "0.1.7")
            selected = initrd.select_generation_for_boot(
                sysroot,
                "a" * 64,
                "0.1.7",
                f"roothash={'a' * 64} veyra.vpkg-generation={trial.name}",
            )
            self.assertEqual(selected, trial)
            self.assertEqual((parent.parent / "active").resolve(), parent)

            initrd.complete_trial_generation(sysroot, "a" * 64, "0.1.7", trial.name)
            self.assertEqual((parent.parent / "active").resolve(), trial)
            rolled_back = initrd.rollback_generation(sysroot, "a" * 64, "0.1.7")
            self.assertEqual(rolled_back, parent)
            self.assertEqual((parent.parent / "active").resolve(), parent)

    def test_foreign_or_corrupt_trial_is_rejected_without_changing_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sysroot = Path(temporary) / "sysroot"
            rpmdb = sysroot / "usr/lib/sysimage/rpm"
            rpmdb.mkdir(parents=True)
            (rpmdb / "Packages.db").write_bytes(b"base rpm db")
            (sysroot / "etc").mkdir()
            (sysroot / "var").mkdir()
            parent = initrd.ensure_base_generation(sysroot, "a" * 64, "0.1.7")
            trial = initrd.create_trial_generation(sysroot, "a" * 64, "0.1.7")

            with self.assertRaisesRegex(initrd.InitrdError, "different deployment identity"):
                initrd.read_generation(trial.parent, trial.name, "b" * 64, "0.1.7")
            self.assertEqual((parent.parent / "active").resolve(), parent)

    def test_mounts_generation_owned_usr_and_etc_uppers_before_switch_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sysroot = Path(temporary) / "sysroot"
            generation = sysroot / "var/lib/veyra/vpkg/system-generations/id/generation-1"
            for relative in (
                "upper/usr", "upper/etc", "work/usr", "work/etc",
                "usr", "etc",
            ):
                (generation / relative if relative.startswith(("upper", "work")) else sysroot / relative).mkdir(
                    parents=True, exist_ok=True
                )
            with patch.object(initrd.subprocess, "run") as run:
                initrd.mount_generation(sysroot, generation)

            self.assertEqual(run.call_args_list, [
                call(
                    ["mount", "-t", "overlay", "overlay", "-o",
                     f"lowerdir={sysroot}/usr,upperdir={generation}/upper/usr,workdir={generation}/work/usr",
                     str(sysroot / "usr")],
                    check=True,
                ),
                call(
                    ["mount", "-t", "overlay", "overlay", "-o",
                     f"lowerdir={sysroot}/etc,upperdir={generation}/upper/etc,workdir={generation}/work/etc",
                     str(sysroot / "etc")],
                    check=True,
                ),
            ])

    def test_selected_uki_is_resolved_under_the_mounted_efi_system_partition(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uki = root / "efi/EFI/Linux/veyra-next_0.1.7.efi"
            uki.parent.mkdir(parents=True)
            uki.write_bytes(b"UKI")
            with patch.object(
                initrd.subprocess, "run", return_value=type("Result", (), {"stdout": "/EFI/Linux/veyra-next_0.1.7.efi\n"})()
            ) as run:
                selected = initrd.selected_uki_path(root / "sysroot", efi_roots=(root / "efi",))

            self.assertEqual(selected, uki)
            run.assert_called_once_with(
                ["bootctl", "--print-stub-path"], check=True, text=True,
                stdout=initrd.subprocess.PIPE, stderr=initrd.subprocess.PIPE,
            )

    def test_selected_uki_rejects_paths_outside_mounted_esp_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = root / "tmp/not-the-esp.efi"
            outside.parent.mkdir()
            outside.write_bytes(b"UKI")
            with patch.object(
                initrd.subprocess, "run", return_value=type("Result", (), {"stdout": str(outside)})()
            ), self.assertRaisesRegex(initrd.InitrdError, "mounted EFI System Partition"):
                initrd.selected_uki_path(root / "sysroot", efi_roots=(root / "efi",))

    def test_trial_copy_preserves_system_symlink_payloads_without_following_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            sysroot = Path(temporary) / "sysroot"
            rpmdb = sysroot / "usr/lib/sysimage/rpm"
            rpmdb.mkdir(parents=True)
            (rpmdb / "Packages.db").write_bytes(b"base rpm db")
            (sysroot / "etc").mkdir()
            (sysroot / "var").mkdir()
            parent = initrd.ensure_base_generation(sysroot, "a" * 64, "0.1.7")
            (parent / "upper/usr/lib64").symlink_to("lib")

            trial = initrd.create_trial_generation(sysroot, "a" * 64, "0.1.7")

            self.assertTrue((trial / "upper/usr/lib64").is_symlink())
            self.assertEqual((trial / "upper/usr/lib64").readlink(), Path("lib"))


if __name__ == "__main__":
    unittest.main()
