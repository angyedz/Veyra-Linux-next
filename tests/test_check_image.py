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


def write_uki(path: Path, version_id: str = "44") -> None:
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
    def make_artifacts(self, directory: Path, *, root_version: str = "44", uki_version: str = "44",
                       systemd: str = "259.1-1.fc44", fedora_release: str = "44-0.1",
                       kernel: str = "7.1.4-200.fc44") -> Path:
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

    def test_accepts_matching_fedora_44_root_uki_and_base_rpms(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = self.make_artifacts(Path(temporary))
            check_image.validate_fedora_release(image)

    def test_rejects_rawhide_root_even_when_uki_is_fedora_44(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = self.make_artifacts(Path(temporary), root_version="46")
            with self.assertRaisesRegex(ValueError, "root artifact must identify Fedora 44"):
                check_image.validate_fedora_release(image)

    def test_rejects_rawhide_uki_osrel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = self.make_artifacts(Path(temporary), uki_version="46")
            with self.assertRaisesRegex(ValueError, "UKI artifact must identify Fedora 44"):
                check_image.validate_fedora_release(image)

    def test_rejects_rawhide_core_rpm_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = self.make_artifacts(Path(temporary), systemd="262.1-1.fc46")
            with self.assertRaisesRegex(ValueError, "systemd RPM must come from Fedora 44"):
                check_image.validate_fedora_release(image)


if __name__ == "__main__":
    unittest.main()
