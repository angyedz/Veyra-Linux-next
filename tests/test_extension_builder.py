from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build-extension-rpm.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_extension_builder", SourceFileLoader("veyra_extension_builder", str(SCRIPT))
)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ExtensionCompatibilityTests(unittest.TestCase):
    def test_package_set_reads_included_mkosi_base_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "mkosi.conf").write_text(
                "[Include]\nInclude=mkosi.veyra-base.conf\n"
                "[Content]\nPackages=top-level-package\n",
                encoding="utf-8",
            )
            (root / "mkosi.veyra-base.conf").write_text(
                "[Content]\nPackages=kernel\nPackages=libdnf5\n",
                encoding="utf-8",
            )

            self.assertEqual(
                builder.package_set_from_mkosi(root / "mkosi.conf"),
                ["top-level-package", "kernel", "libdnf5"],
            )

    def test_extension_release_metadata_uses_veyra_abi_level(self) -> None:
        self.assertEqual(
            builder.extension_release_metadata("44", "1"),
            "ID=fedora\nVERSION_ID=44\nSYSEXT_LEVEL=1\n",
        )

    def test_extension_release_metadata_rejects_invalid_abi_level(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "compatibility level"):
            builder.extension_release_metadata("44", "../host")


if __name__ == "__main__":
    unittest.main()
