from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare-vpkg-image-test.py"
SPEC = importlib.util.spec_from_loader(
    "prepare_vpkg_image", SourceFileLoader("prepare_vpkg_image", str(SCRIPT))
)
assert SPEC and SPEC.loader
prepare = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prepare)


class PrepareVpkgImageTests(unittest.TestCase):
    def test_guest_test_bundle_contains_runtime_imports_and_update_definitions(self) -> None:
        required = {
            Path("mkosi.extra/usr/lib/veyra/veyra_repo.py"),
            Path("mkosi.extra/usr/lib/veyra/vpkg_libdnf.py"),
            Path("mkosi.extra/usr/share/veyra/vpkg/protected-core.json"),
            Path("mkosi.extra/usr/lib/sysupdate.d/10-root-verity.transfer"),
            Path("mkosi.extra/usr/lib/sysupdate.d/20-root.transfer"),
            Path("mkosi.extra/usr/lib/sysupdate.d/99-uki.transfer"),
            Path("mkosi.images/initrd/mkosi.extra/usr/libexec/veyra-vpkg-initrd.py"),
        }
        sources = set(prepare.unit_test_sources())
        self.assertTrue(required <= sources)
        self.assertTrue(all((prepare.PROJECT_ROOT / path).is_file() for path in required))

    def test_upstream_repo_configs_are_read_and_removed_with_privileges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_directory = Path(temporary)
            fedora = repo_directory / "fedora.repo"
            veyra = repo_directory / "veyra-testing.repo"
            fedora.touch()
            veyra.touch()
            content = (
                "[fedora]\nbaseurl=file:///source-fedora\n"
                "[rpmfusion-free]\nbaseurl=file:///source-rpmfusion\n"
            )

            with patch.object(
                prepare,
                "run",
                return_value=prepare.subprocess.CompletedProcess([], 0, content, ""),
            ) as run:
                prepare.remove_upstream_repo_configs(repo_directory)

            self.assertEqual(
                run.call_args_list,
                [
                    call(["cat", str(fedora)], privileged=True),
                    call(["rm", "-f", str(fedora)], privileged=True),
                ],
            )

    def test_veyra_repo_configs_are_not_read_or_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo_directory = Path(temporary)
            veyra = repo_directory / "veyra-testing.repo"
            veyra.touch()
            with patch.object(prepare, "run") as run:
                prepare.remove_upstream_repo_configs(repo_directory)

            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
