from __future__ import annotations

import importlib.util
import json
import os
from importlib.machinery import SourceFileLoader
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "mkosi.extra/usr/bin/vforge"
SPEC = importlib.util.spec_from_loader("veyra_vforge", SourceFileLoader("veyra_vforge", str(SCRIPT)))
assert SPEC and SPEC.loader
vforge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vforge)


class VforgeNativeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {
            "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_named_environment_persists_overlay_upper_and_work_directories(self) -> None:
        path = vforge.create("backend")

        metadata = json.loads((path / "environment.json").read_text())
        self.assertEqual(metadata["backend"], "overlayfs")
        self.assertEqual(metadata["lower"], "/")
        self.assertTrue(Path(metadata["upper"]).is_dir())
        self.assertTrue(Path(metadata["work"]).is_dir())
        self.assertEqual(vforge.environment_metadata(path), metadata)

    def test_project_environment_stays_in_project_vforge_directory(self) -> None:
        project = self.root / "source"
        project.mkdir()
        (project / "main.py").write_text("print('hello')")

        path = vforge.create("backend", project=project)

        self.assertEqual(path, project / ".vforge" / "backend")
        metadata = vforge.environment_metadata(path)
        self.assertEqual(metadata["project"], str(project))
        self.assertTrue((project / "main.py").is_file())

    def test_project_mounts_are_explicit_and_read_only_by_default(self) -> None:
        shared = self.root / "shared"
        shared.mkdir()

        mounts = vforge.normalize_mounts([{"source": str(shared), "target": "/workspace/shared"}])

        self.assertEqual(mounts, [{"source": str(shared), "target": "/workspace/shared", "access": "read-only"}])

    def test_rejects_mount_targets_that_can_replace_system_paths_or_escape_root(self) -> None:
        for target in ("/", "/usr/local", "/etc", "/../host", "relative/path"):
            with self.subTest(target=target), self.assertRaises(vforge.VforgeError):
                vforge.checked_mount_target(target)

    def test_rejects_symlink_mount_sources(self) -> None:
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)

        with self.assertRaisesRegex(vforge.VforgeError, "mount source"):
            vforge.normalize_mounts([{"source": str(link), "target": "/workspace/shared"}])

    def test_project_configuration_must_not_be_a_symlink(self) -> None:
        project = self.root / "source"
        project.mkdir()
        config = self.root / "external-config.toml"
        config.write_text('[environment]\nname = "backend"\n')
        (project / ".vforge.toml").symlink_to(config)

        with self.assertRaisesRegex(vforge.VforgeError, "configuration may not be a symlink"):
            vforge.project_settings(project)

    def test_cloned_current_root_mounts_are_marked_for_read_only_remount(self) -> None:
        lower = Path("/run/user/1000/vforge/env/lower")
        mountinfo = (
            "40 30 0:1 / / rw - rootfs rootfs rw\n"
            "41 40 0:2 / /etc rw - ext4 /dev/disk rw\n"
            "42 40 0:3 / /run/user/1000 rw - tmpfs tmpfs rw\n"
            "43 42 0:4 / /run/user/1000/shared\\040docs rw - ext4 /dev/sdb rw\n"
            f"50 40 0:1 / {lower} rw - rootfs rootfs rw\n"
            f"51 50 0:2 / {lower}/etc rw - ext4 /dev/disk rw\n"
            f"52 50 0:3 / {lower}/run rw - tmpfs tmpfs rw\n"
        )

        targets = vforge.cloned_mount_targets(mountinfo, lower)

        self.assertEqual(targets, [lower / "run", lower / "etc", lower])

    def test_invalid_environment_metadata_fails_closed(self) -> None:
        path = self.root / "legacy"
        path.mkdir()
        (path / "environment.json").write_text('{"schema": 1, "backend": "unknown"}')

        with self.assertRaisesRegex(vforge.VforgeError, "unsupported Vforge backend"):
            vforge.environment_metadata(path)

    def test_namespace_command_selects_user_mount_pid_and_network_namespaces(self) -> None:
        path = vforge.create("backend")
        with patch.object(vforge.subprocess, "run", return_value=type("Result", (), {"returncode": 0})()) as run:
            self.assertEqual(vforge.namespace_command(path, ["/bin/true"]), 0)

        command = run.call_args.args[0]
        self.assertIn("--user", command)
        self.assertIn("--mount", command)
        self.assertIn("--pid", command)
        self.assertIn("--net", command)

    def test_storage_root_can_be_selected_and_reused(self) -> None:
        selected = self.root / "persistent-vforge"
        vforge.main(["create", "--storage-root", str(selected), "backend"])

        self.assertEqual(vforge.store_root(), selected)
        self.assertTrue((selected / "environments" / "backend" / "upper").is_dir())


if __name__ == "__main__":
    unittest.main()
