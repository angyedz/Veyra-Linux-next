from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check-build-environment.py"
SPEC = importlib.util.spec_from_loader(
    "veyra_check_build_environment", SourceFileLoader("veyra_check_build_environment", str(SCRIPT))
)
assert SPEC and SPEC.loader
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


class BuildEnvironmentGateTests(unittest.TestCase):
    def validate(self, *, repos: dict[str, str], listing: str, osrelease: str = "ID=fedora\nVERSION_ID=44\n",
                 mounts: str = "", in_container: bool = True) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            release_file = root / "os-release"
            release_file.write_text(osrelease, encoding="utf-8")
            repo_dir = root / "repos"
            repo_dir.mkdir()
            for filename, content in repos.items():
                (repo_dir / filename).write_text(content, encoding="utf-8")
            gate.validate_environment(
                osrelease_path=release_file,
                repo_directory=repo_dir,
                mkosi_config=gate.ROOT / "mkosi.conf",
                enabled_repo_listing=listing,
                mountinfo=mounts,
                in_container=in_container,
            )

    def fedora_repos(self) -> dict[str, str]:
        return {
            "fedora.repo": (
                "[fedora]\n"
                "metalink=https://mirrors.fedoraproject.org/metalink?repo=fedora-$releasever&arch=$basearch\n"
                "enabled=1\n[fedora-source]\nenabled=0\n"
            ),
            "updates.repo": (
                "[updates]\n"
                "metalink=https://mirrors.fedoraproject.org/metalink?repo=updates-released-f$releasever&arch=$basearch\n"
                "enabled=1\n"
            ),
            "openh264.repo": (
                "[fedora-cisco-openh264]\n"
                "metalink=https://mirrors.fedoraproject.org/metalink?repo=fedora-cisco-openh264-$releasever&arch=$basearch\n"
                "enabled=1\n"
            ),
        }

    def fedora_listing(self) -> str:
        return (
            "repo id repo name\nfedora Fedora 44\nupdates Fedora 44 Updates\n"
            "fedora-cisco-openh264 Fedora 44 openh264\n"
        )

    def test_accepts_only_official_fedora_target_repositories(self) -> None:
        self.validate(
            repos=self.fedora_repos(),
            listing=self.fedora_listing(),
            mounts="36 25 0:32 / / rw - overlay overlay rw\n37 36 0:45 /work /work rw - ext4 /dev/sda1 rw\n",
        )

    def test_rejects_mismatched_container_release(self) -> None:
        with self.assertRaisesRegex(gate.BuildEnvironmentError, "VERSION_ID"):
            self.validate(repos={}, listing="repo id repo name\n", osrelease="ID=fedora\nVERSION_ID=46\n")

    def test_rejects_enabled_host_copr(self) -> None:
        repos = self.fedora_repos()
        repos["copr.repo"] = (
            "[copr:hyprland]\n"
            "baseurl=https://download.copr.fedorainfracloud.org/results/solopasha/hyprland/fedora-rawhide-x86_64/\n"
            "enabled=1\n"
        )
        with self.assertRaisesRegex(gate.BuildEnvironmentError, "enabled repos"):
            self.validate(repos=repos, listing=self.fedora_listing() + "copr:hyprland COPR\n")

    def test_rejects_fedora_repo_id_overridden_to_copr(self) -> None:
        repos = self.fedora_repos()
        repos["fedora.repo"] = (
            "[fedora]\nbaseurl=https://download.copr.fedorainfracloud.org/results/evil/rawhide/\nenabled=1\n"
        )
        with self.assertRaisesRegex(gate.BuildEnvironmentError, "Fedora repository URL"):
            self.validate(repos=repos, listing=self.fedora_listing())

    def test_rejects_host_cache_bind_mount(self) -> None:
        with self.assertRaisesRegex(gate.BuildEnvironmentError, "host DNF/cache"):
            self.validate(
                repos=self.fedora_repos(),
                listing=self.fedora_listing(),
                mounts=("36 25 0:32 / / rw - overlay overlay rw\n"
                        "37 36 8:1 /home/user/.cache/mkosi /root/.cache/mkosi rw - ext4 /dev/sda1 rw\n"),
            )

    def test_allows_only_github_actions_home_bind_mount(self) -> None:
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true", "HOME": "/github/home"}):
            self.validate(
                repos=self.fedora_repos(),
                listing=self.fedora_listing(),
                mounts=("36 25 0:32 / / rw - overlay overlay rw\n"
                        "37 36 8:1 /home/runner/work/_temp/_github_home /github/home rw - ext4 /dev/sda1 rw\n"),
            )

    def test_rejects_github_home_mount_outside_actions(self) -> None:
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "false", "HOME": "/github/home"}):
            with self.assertRaisesRegex(gate.BuildEnvironmentError, "host DNF/cache"):
                self.validate(
                    repos=self.fedora_repos(),
                    listing=self.fedora_listing(),
                    mounts=("36 25 0:32 / / rw - overlay overlay rw\n"
                            "37 36 8:1 /home/runner/work/_temp/_github_home /github/home rw - ext4 /dev/sda1 rw\n"),
                )

    def test_still_rejects_github_actions_cache_bind_mount(self) -> None:
        with patch.dict("os.environ", {"GITHUB_ACTIONS": "true"}):
            with self.assertRaisesRegex(gate.BuildEnvironmentError, "host DNF/cache"):
                self.validate(
                    repos=self.fedora_repos(),
                    listing=self.fedora_listing(),
                    mounts=("36 25 0:32 / / rw - overlay overlay rw\n"
                            "37 36 8:1 /home/runner/.cache /github/home/.cache rw - ext4 /dev/sda1 rw\n"),
                )

    def test_rejects_non_container_host_build(self) -> None:
        with self.assertRaisesRegex(gate.BuildEnvironmentError, "isolated Fedora container"):
            self.validate(repos={}, listing="repo id repo name\n", in_container=False)


if __name__ == "__main__":
    unittest.main()
