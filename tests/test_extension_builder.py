from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build-extension-rpm.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_extension_builder", SourceFileLoader("veyra_extension_builder", str(SCRIPT))
)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(builder)


class ExtensionCompatibilityTests(unittest.TestCase):
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
