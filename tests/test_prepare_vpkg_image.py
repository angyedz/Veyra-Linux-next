from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
