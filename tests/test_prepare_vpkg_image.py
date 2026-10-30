from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/prepare-vpkg-image-test.py"
SPEC = importlib.util.spec_from_loader(
    "prepare_vpkg_image", SourceFileLoader("prepare_vpkg_image", str(SCRIPT))
)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PrepareVpkgImageTests(unittest.TestCase):
    def test_guest_test_bundle_contains_test_imports_and_runtime_sources(self) -> None:
        required = {
            Path("mkosi.conf"),
            Path("scripts/check-build-environment.py"),
            Path("scripts/check-image.py"),
            Path("mkosi.extra/usr/lib/veyra/veyra_repo.py"),
            Path("mkosi.extra/usr/lib/veyra/vpkg_libdnf.py"),
            Path("mkosi.extra/usr/lib/sysupdate.d/10-root-verity.transfer"),
            Path("mkosi.extra/usr/lib/sysupdate.d/20-root.transfer"),
            Path("mkosi.extra/usr/lib/sysupdate.d/99-uki.transfer"),
        }
        sources = set(prepare.unit_test_sources())
        self.assertTrue(required <= sources)
        self.assertTrue(all((ROOT / path).is_file() for path in required))


    def test_guest_has_complete_tracked_source_contract_not_a_partial_test_copy(self) -> None:
        # This suite runs on the host and again inside the disposable guest.
        # A guest formerly received tests/ and a few imports only, yielding
        # 24 false errors when tests read workflows, installer and BIOS sources.
        required = {
            ".github/workflows/release.yml",
            ".github/workflows/native-live-iso.yml",
            "mkosi.extra/usr/bin/v-dri",
            "mkosi.extra/usr/bin/vforge",
            "mkosi.extra/usr/lib/sysupdate.d/80-bios-kernel.transfer",
            "mkosi.extra/usr/lib/sysupdate.d/90-bios-initrd.transfer",
            "scripts/veyra-install.py",
            "scripts/test-sysupdate-image.py",
            "tests/test_sysupdate_image.py",
            "tests/test_update_keyring_contract.py",
        }
        self.assertTrue(
            all((ROOT / item).is_file() for item in required),
            "Guest test bundle must include all tracked source contracts",
        )
        if not (ROOT / ".git").is_dir():
            # The intentionally clean guest snapshot contains no .git.
            return
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary) / "tracked-tests.tar"
            subprocess.run(
                [
                    "git", "-C", str(ROOT), "archive", "--format=tar",
                    f"--output={archive}", "HEAD",
                ],
                check=True, capture_output=True,
            )
            with tarfile.open(archive, "r") as stream:
                members = set(stream.getnames())
            self.assertTrue(required <= members)
            self.assertFalse(any(name.startswith((".git/", "build/", "mkosi.output/"))
                                 for name in members))
            self.assertLess(archive.stat().st_size, 64 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
