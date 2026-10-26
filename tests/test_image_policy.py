"""Source-level invariants for the immutable image's early boot and build policy.

These checks are intentionally offline; they do not claim that a VM boot works.
"""
from __future__ import annotations

import pathlib
import re
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def entries(path: pathlib.Path, section: str) -> list[tuple[str, str]]:
    current = None
    found = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
        elif current == section:
            if "=" not in line:
                raise AssertionError(f"malformed {section} entry: {line}")
            key, value = line.split("=", 1)
            found.append((key.strip(), value.strip()))
    return found


class ImagePolicyContract(unittest.TestCase):
    def test_uefi_unsigned_prototype_is_explicit(self):
        config = ROOT / "mkosi.conf"
        content = entries(config, "Content")
        runtime = entries(config, "Runtime")
        self.assertIn(("Bootable", "yes"), content)
        self.assertIn(("Bootloader", "systemd-boot"), content)
        self.assertIn(("UnifiedKernelImages", "unsigned"), content)
        self.assertIn(("Firmware", "uefi"), runtime)

    def test_immutable_base_not_accidentally_claimed_in_main(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("not a usable installer or pre-alpha release yet", readme)
        self.assertIn("not implemented", readme)

    def test_required_service_preset_is_explicit(self):
        preset = ROOT / "mkosi.extra/usr/lib/systemd/system-preset/99-veyra.preset"
        enabled = {
            line.strip().split(maxsplit=1)[1]
            for line in preset.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("enable ")
        }
        self.assertGreaterEqual(enabled, {
            "NetworkManager.service",
            "firewalld.service",
            "plasma-setup.service",
            "plasmalogin.service",
        })

    def test_builder_pinned_to_full_commit(self):
        script = (ROOT / "scripts/mkosi.sh").read_text(encoding="utf-8")
        self.assertRegex(script, r"git\+https://github\.com/systemd/mkosi@[0-9a-f]{40}")
        self.assertIn('mkosi "$@"', script)

    def test_flathub_source_is_vendored(self):
        script = (ROOT / "mkosi.postinst.chroot").read_text(encoding="utf-8")
        self.assertIn("flatpak remote-add --system --if-not-exists flathub", script)
        self.assertIn("/usr/share/veyra/flathub.flatpakrepo", script)
        source = (ROOT / "mkosi.extra/usr/share/veyra/flathub.flatpakrepo").read_text(encoding="utf-8")
        self.assertIn("[Flatpak Repo]", source)
        self.assertIn("Url=https://dl.flathub.org/repo/", source)
        self.assertRegex(source, r"(?m)^GPGKey=\S+")


if __name__ == "__main__":
    unittest.main()
