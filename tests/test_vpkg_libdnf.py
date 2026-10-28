"""The Fedora libdnf5 SWIG repository contract must remain fail-closed.

These tests do not substitute for installing a signed RPM in a real QEMU guest.
"""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mkosi.extra/usr/lib/veyra"))
import vpkg_libdnf  # noqa: E402

PINNED_KEY = "file:///usr/share/veyra/RPM-GPG-KEY-VEYRA"


class _Option:
    def __init__(self) -> None:
        self.value = None

    def set(self, *args) -> None:
        self.value = args[-1]


class _Config:
    def __init__(self) -> None:
        self.options = {}

    def __getattr__(self, name):
        if name.startswith("get_") and name.endswith("_option"):
            key = name[4:-7]
            return lambda: self.options.setdefault(key, _Option())
        raise AttributeError(name)


class _Repo:
    def __init__(self, repo_id) -> None:
        self.repo_id = repo_id
        self.config = _Config()

    def get_id(self):
        return self.repo_id

    def get_config(self):
        return self.config

    def set_callbacks(self, *_args):
        raise AssertionError("Python RepoCallbacks cannot be passed to std::unique_ptr on Fedora 44")


class _RepoSack:
    def __init__(self, repos):
        self.repos = repos
        self.loaded = False

    def create_repos_from_system_configuration(self):
        return None

    def load_repos(self):
        self.loaded = True


class _Vars:
    Priority_RUNTIME = 42

    def get(self):
        return self

    def set(self, *_args):
        return None


class _Base:
    def __init__(self, repos):
        self.config = _Config()
        self.repos = _RepoSack(repos)

    def get_config(self):
        return self.config

    def load_config(self):
        return None

    def get_vars(self):
        return _Vars()

    def setup(self):
        return None

    def get_repo_sack(self):
        return self.repos


class _Bindings:
    def __init__(self, base):
        class BaseModule:
            pass
        self.base = BaseModule()
        self.base.Base = lambda: base
        class RepoModule:
            pass
        self.repo = RepoModule()
        self.repo.RepoQuery = lambda _base: base.repos.repos


class VpkgLibdnfRepoSecurityTests(unittest.TestCase):
    def test_veyra_repos_pin_key_and_keep_metadata_and_package_gpg_checks(self):
        repos = [_Repo("veyra-main"), _Repo("veyra-testing"), _Repo("fedora")]
        base = _Base(repos)
        with (
            patch.object(vpkg_libdnf, "_bindings", return_value=_Bindings(base)),
            patch.object(
                vpkg_libdnf.Session, "_resolve_veyra_repositories",
                return_value={
                    "veyra-main": Path("/tmp/testing-main"),
                    "veyra-testing": Path("/tmp/testing-edge"),
                },
            ),
        ):
            session = vpkg_libdnf.Session(
                installroot=Path("/tmp/vpkg-isolated-root"),
                releasever="44",
                repo_ids=["veyra-main", "veyra-testing"],
                cache_dir=Path("/tmp/vpkg-cache"),
                history_dir=Path("/tmp/vpkg-history"),
                persist_dir=Path("/tmp/vpkg-persist"),
                state_dir=Path("/tmp/vpkg-state"),
            )
        self.assertTrue(base.repos.loaded)
        for repo in repos[:2]:
            with self.subTest(repo=repo.repo_id):
                conf = repo.config.options
                self.assertEqual(conf["gpgkey"].value, [PINNED_KEY])
                self.assertIs(conf["repo_gpgcheck"].value, True)
                self.assertIs(conf["pkg_gpgcheck"].value, True)
                self.assertIs(conf["enabled"].value, True)
                self.assertEqual(len(conf["baseurl"].value), 1)
        self.assertIs(repos[2].config.options["enabled"].value, False)
        self.assertIsNotNone(session.base)

    def test_sources_keep_pinned_trust_in_production_repo_files(self):
        for name in ("veyra-main", "veyra-testing"):
            data = (ROOT / f"mkosi.extra/etc/yum.repos.d/{name}.repo").read_text()
            self.assertIn("repo_gpgcheck=1", data)
            self.assertIn("gpgcheck=1", data)
            self.assertIn(f"gpgkey={PINNED_KEY}", data)


if __name__ == "__main__":
    unittest.main()
