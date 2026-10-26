from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
from pathlib import Path
import struct
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-image.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_check_image", SourceFileLoader("veyra_check_image", str(SCRIPT))
)
assert SPEC and SPEC.loader
check_image = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_image)


def write_uki(path: Path, version_id: str | None = None) -> None:
    if version_id is None:
        version_id = check_image.configured_fedora_release()
    osrel = (
        f'ID=fedora\nVERSION_ID={version_id}\nIMAGE_ID="veyra-next"\n'
        'IMAGE_VERSION="0.1.0"\n'
    ).encode()
    image = bytearray(0x600)
    pe_offset = 0x80
    struct.pack_into("<I", image, 0x3C, pe_offset)
    image[pe_offset : pe_offset + 4] = b"PE\0\0"
    struct.pack_into("<H", image, pe_offset + 6, 1)
    struct.pack_into("<H", image, pe_offset + 20, 0xF0)
    section = pe_offset + 24 + 0xF0
    image[section : section + 5] = b".osrel"
    struct.pack_into("<I", image, section + 8, len(osrel))
    struct.pack_into("<I", image, section + 16, 0x100)
    struct.pack_into("<I", image, section + 20, 0x300)
    image[0x300 : 0x300 + len(osrel)] = osrel
    path.write_bytes(image)


class FedoraReleaseGateTests(unittest.TestCase):
    def make_artifacts(
        self,
        directory: Path,
        *,
        root_version: str | None = None,
        uki_version: str | None = None,
        systemd: str | None = None,
        fedora_release: str | None = None,
        kernel: str | None = None,
    ) -> Path:
        target_release = check_image.configured_fedora_release()
        root_version = root_version if root_version is not None else target_release
        uki_version = uki_version if uki_version is not None else target_release
        systemd = systemd if systemd is not None else f"259.1-1.fc{target_release}"
        fedora_release = fedora_release if fedora_release is not None else f"{target_release}-0.1"
        kernel = kernel if kernel is not None else f"7.1.4-200.fc{target_release}"
        image = directory / "veyra-next.raw"
        image.touch()
        (directory / "veyra-next.osrelease").write_text(
            f'ID=fedora\nVERSION_ID={root_version}\nIMAGE_ID="veyra-next"\nIMAGE_VERSION="0.1.0"\n',
            encoding="utf-8",
        )
        write_uki(directory / "veyra-next.efi", uki_version)
        packages = [
            {"name": "fedora-release", "version": fedora_release},
            {"name": "systemd", "version": systemd},
            {"name": "systemd-udev", "version": systemd},
            {"name": "kernel-core", "version": kernel},
        ]
        (directory / "veyra-next.manifest").write_text(
            json.dumps({"packages": packages}), encoding="utf-8"
        )
        return image

    def test_accepts_matching_configured_fedora_root_uki_and_base_rpms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = self.make_artifacts(Path(temporary))
            check_image.validate_fedora_release(image)

    def test_accepts_a_one_mib_grub_bios_boot_partition(self) -> None:
        check_image.validate_bios_boot_partition(
            [
                {"type": check_image.BIOS_BOOT_TYPE.upper(), "size": 2048},
                {"type": check_image.EFI_SYSTEM_TYPE.upper(), "size": 2048},
            ]
        )

    def test_rejects_missing_or_undersized_bios_boot_partition(self) -> None:
        with self.assertRaisesRegex(ValueError, "expected one"):
            check_image.validate_bios_boot_partition([])
        with self.assertRaisesRegex(ValueError, "at least 1 MiB"):
            check_image.validate_bios_boot_partition(
                [
                    {"type": check_image.BIOS_BOOT_TYPE, "size": 2047},
                    {"type": check_image.EFI_SYSTEM_TYPE, "size": 2048},
                ]
            )
        with self.assertRaisesRegex(ValueError, "EFI System Partition"):
            check_image.validate_bios_boot_partition(
                [{"type": check_image.BIOS_BOOT_TYPE, "size": 2048}]
            )

    def test_rejects_root_from_a_different_fedora_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target_release = check_image.configured_fedora_release()
            foreign_release = str(int(target_release) + 1)
            image = self.make_artifacts(
                Path(temporary), root_version=foreign_release, uki_version=foreign_release
            )
            with self.assertRaisesRegex(
                ValueError, f"root artifact must identify Fedora {target_release}"
            ):
                check_image.validate_fedora_release(image)

    def test_rejects_uki_from_a_different_fedora_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target_release = check_image.configured_fedora_release()
            foreign_release = str(int(target_release) + 1)
            image = self.make_artifacts(Path(temporary), uki_version=foreign_release)
            with self.assertRaisesRegex(
                ValueError, f"UKI artifact must identify Fedora {target_release}"
            ):
                check_image.validate_fedora_release(image)

    def test_rejects_core_rpm_from_a_newer_fedora_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target_release = check_image.configured_fedora_release()
            foreign_release = int(target_release) + 1
            image = self.make_artifacts(
                Path(temporary), systemd=f"262.1-1.fc{foreign_release}"
            )
            with self.assertRaisesRegex(ValueError, "newer Fedora appears"):
                check_image.validate_fedora_release(image)

    def test_rejects_any_newer_fedora_rpm_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = self.make_artifacts(Path(temporary))
            foreign_release = int(check_image.configured_fedora_release()) + 1
            manifest = image.with_name("veyra-next.manifest")
            content = json.loads(manifest.read_text(encoding="utf-8"))
            content["packages"].append(
                {"name": "some-library", "version": f"1.0-1.fc{foreign_release}"}
            )
            manifest.write_text(json.dumps(content), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "newer Fedora appears"):
                check_image.validate_fedora_release(image)

    def test_rejects_rawhide_reference_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = self.make_artifacts(Path(temporary))
            manifest = image.with_name("veyra-next.manifest")
            content = json.loads(manifest.read_text(encoding="utf-8"))
            content["packages"].append({"name": "rawhide-only-package", "version": "1.0-1.fc44"})
            manifest.write_text(json.dumps(content), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Rawhide package"):
                check_image.validate_fedora_release(image)


if __name__ == "__main__":
    unittest.main()
