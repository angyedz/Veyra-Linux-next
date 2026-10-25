from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare-vpkg-image-test.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_prepare_vpkg_image", SourceFileLoader("veyra_prepare_vpkg_image", str(SCRIPT))
)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PrepareVpkgImageTests(unittest.TestCase):
    def test_removes_only_upstream_repo_files_with_privileged_unlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_dir = Path(temporary)
            fedora = repo_dir / "fedora.repo"
            fedora.write_text("[fedora]\n", encoding="utf-8")
            fedora_updates = repo_dir / "updates.repo"
            fedora_updates.write_text("[updates]\n", encoding="utf-8")
            veyra = repo_dir / "veyra-testing.repo"
            veyra.write_text("[veyra-testing]\n", encoding="utf-8")
            unrelated = repo_dir / "custom.repo"
            unrelated.write_text("[custom]\n", encoding="utf-8")

            with patch.object(prepare, "run") as run:
                prepare.disable_upstream_repository_files(repo_dir)

        self.assertEqual(
            run.call_args_list,
            [
                unittest.mock.call(["unlink", str(fedora)], privileged=True),
                unittest.mock.call(["unlink", str(fedora_updates)], privileged=True),
            ],
        )


if __name__ == "__main__":
    unittest.main()
