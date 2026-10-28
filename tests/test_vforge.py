from __future__ import annotations

import importlib.util
import json
import os
from importlib.machinery import SourceFileLoader
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import call, patch


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

    def test_root_base_must_be_read_only_erofs(self) -> None:
        mountinfo = "40 30 0:1 / / ro,relatime - erofs /dev/mapper/veyra-root ro\n"
        vforge.verify_immutable_root_mount(mountinfo)
        with self.assertRaisesRegex(vforge.VforgeError, "EROFS root"):
            vforge.verify_immutable_root_mount(
                "40 30 0:1 / / rw,relatime - overlay overlay rw\n"
            )

    def test_vforge_base_must_be_read_only_bind_of_active_veyra_root(self) -> None:
        mountinfo = (
            "40 30 0:41 / / ro,relatime - erofs /dev/mapper/veyra-root ro\n"
            "45 40 0:41 / /run/veyra/vforge/base ro,relatime - erofs /dev/mapper/veyra-root ro\n"
        )
        vforge.verify_vforge_base_mount(mountinfo)
        invalid_mounts = (
            mountinfo.replace("0:41 / /run/veyra", "0:42 / /run/veyra"),
            mountinfo.replace("0:41 / /run/veyra", "0:41 /subdir /run/veyra"),
            mountinfo.replace("ro,relatime - erofs /dev/mapper/veyra-root ro\n", "rw,relatime - erofs /dev/mapper/veyra-root rw\n", 1),
            mountinfo.replace("0:41 / /run/veyra/vforge/base ro,relatime - erofs", "0:41 / /run/veyra/vforge/base ro,relatime - overlay"),
        )
        for invalid in invalid_mounts:
            with self.subTest(mountinfo=invalid), self.assertRaisesRegex(vforge.VforgeError, "EROFS root|base mount"):
                vforge.verify_vforge_base_mount(invalid)

    def test_vforge_marker_identifies_active_veyra_root_and_overlay(self) -> None:
        root = self.root / "root"
        (root / "usr/lib").mkdir(parents=True)
        (root / "usr/lib/os-release").write_text(
            'ID=fedora\nVERSION_ID=44\nIMAGE_ID=veyra-next\nIMAGE_VERSION="0.1.0"\n',
            encoding="utf-8",
        )
        cmdline = self.root / "cmdline"
        roothash = "b" * 64
        cmdline.write_text(f"quiet roothash={roothash} boot=veyra\n", encoding="ascii")

        marker = vforge.veyra_root_marker({"root_id": "a" * 32}, root, cmdline)

        self.assertEqual(marker["root_id"], "a" * 32)
        self.assertEqual(marker["base_digest"], roothash)
        self.assertEqual(marker["storage_driver"], "overlayfs")

    def test_vpkg_state_is_created_inside_private_overlay_root(self) -> None:
        root = self.root / "root"
        (root / "usr/lib/sysimage/rpm").mkdir(parents=True)
        vforge.ensure_vpkg_state(root)

        self.assertTrue((root / "usr/lib/sysimage/rpm").is_dir())
        self.assertTrue((root / "var/lib/veyra/vpkg/container/history").is_dir())
        self.assertTrue((root / "var/cache/veyra/vpkg/container").is_dir())

    def test_invalid_environment_metadata_fails_closed(self) -> None:
        path = self.root / "legacy"
        path.mkdir()
        path.chmod(0o700)
        metadata = path / "environment.json"
        metadata.write_text('{"schema": 1, "backend": "unknown"}')
        metadata.chmod(0o600)

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

    def test_environment_uses_a_nonrecursive_veyra_bind_as_overlay_lower(self) -> None:
        environment = vforge.create("backend")
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o700)
        mounted: list[str] = []
        commands: list[list[str]] = []

        def fake_run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
            commands.append(command)
            if command[0] == "mount" and command[1] != "--make-rprivate" and command[1] != "-o":
                mounted.append(command[-1])
            return subprocess.CompletedProcess(command, 0, "", "")

        def fake_bind_mount(source: str, target: str, *, readonly: bool = False, mountpoints=None) -> None:
            mounted.append(target)
            if mountpoints is not None:
                mountpoints.append(target)

        with (
            patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}),
            patch.object(vforge, "verify_vforge_base_mount"),
            patch.object(vforge, "veyra_root_marker", return_value={}),
            patch.object(vforge, "run", side_effect=fake_run),
            patch.object(vforge, "mount_in_namespace", side_effect=fake_bind_mount),
            patch.object(vforge.os, "fork", return_value=12345),
            patch.object(vforge.os, "waitpid", return_value=(12345, 0)),
        ):
            self.assertEqual(vforge.run_environment(environment, ["/bin/true"]), 0)

        unmounted = [command[-1] for command in commands if command[0] == "umount"]
        self.assertEqual(unmounted, list(reversed(mounted)), f"mounted={mounted!r}")
        lower_binds = [
            command for command in commands
            if command[:2] == ["mount", "--bind"] and command[2] == vforge.VFORGE_BASE_ROOT
            and command[-1].endswith("/lower")
        ]
        self.assertEqual(len(lower_binds), 1)
        # Fedora's EROFS-owned /var cannot be written by rootless users.
        # The child gets a persisted user-owned environment /var, never
        # the host's persistent /var through a recursive bind.
        private_var = environment / "var"
        self.assertTrue(private_var.is_dir())
        self.assertEqual(private_var.stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            len([
                command for command in commands
                if command[:3] == ["mount", "--bind", str(private_var)]
                and command[-1].endswith("/merged/var")
            ]),
            1,
        )
        self.assertFalse(any(command[:3] == ["mount", "--bind", "/var"] for command in commands))
        test_state = private_var / "vpkg-generation-marker"
        test_state.write_text("survives across sessions", encoding="utf-8")
        self.assertEqual(test_state.read_text(encoding="utf-8"), "survives across sessions")
        # Fedora OTA QEMU previously failed on this exact rootless copy-up:
        # creating /usr/share/veyra-vforge-ci-overlay-marker. Overlay parents
        # must exist in the private upper *before* the overlay mount.
        self.assertTrue((environment / "upper/usr/share").is_dir())
        self.assertEqual((environment / "upper/usr/share").stat().st_uid, os.getuid())
        overlay_mount = next(command for command in commands if command[:3] == ["mount", "-t", "overlay"])
        self.assertIn("userxattr", overlay_mount[overlay_mount.index("-o") + 1].split(","))
        self.assertFalse(any(command[:2] == ["mount", "--rbind"] for command in commands))
        self.assertFalse(
            any(command[:3] == ["mount", "-o", "remount,bind,ro"] and command[-1].endswith("/lower")
                for command in commands)
        )
        self.assertEqual(list((runtime / "vforge").iterdir()), [])

    def test_rootless_overlay_upper_preseeds_writable_usr_without_touching_base(self) -> None:
        path = vforge.create("backend")
        metadata = vforge.environment_metadata(path)
        upper = Path(metadata["upper"])
        vforge.prepare_rootless_overlay_upper(upper)
        for relative in ("usr", "usr/bin", "usr/lib", "usr/lib64", "usr/share", "etc", "opt"):
            target = upper / relative
            self.assertTrue(target.is_dir(), relative)
            self.assertFalse(target.is_symlink(), relative)
            self.assertEqual(target.stat().st_uid, os.getuid())
            self.assertTrue(target.stat().st_mode & 0o200, relative)
            self.assertFalse(target.stat().st_mode & 0o002, relative)
        self.assertFalse((upper / "usr/share/veyra-vforge-ci-overlay-marker").exists())
        # Reentry must retain all writes and never clear the persistent upper.
        marker = upper / "usr/share/veyra-vforge-ci-overlay-marker"
        marker.write_text("persistent lower-independent content", encoding="utf-8")
        vforge.prepare_rootless_overlay_upper(upper)
        self.assertEqual(marker.read_text(encoding="utf-8"), "persistent lower-independent content")

    def test_rootless_overlay_upper_rejects_symlink_to_outside(self) -> None:
        path = vforge.create("backend")
        upper = Path(vforge.environment_metadata(path)["upper"])
        outside = self.root / "outside"
        outside.mkdir()
        (upper / "usr").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(vforge.VforgeError, "unsafe or non-writable"):
            vforge.prepare_rootless_overlay_upper(upper)
        self.assertFalse((outside / "share").exists())

    def test_rootless_overlay_upper_rejects_unwritable_parent(self) -> None:
        path = vforge.create("backend")
        upper = Path(vforge.environment_metadata(path)["upper"])
        (upper / "usr").mkdir(mode=0o500)
        with self.assertRaisesRegex(vforge.VforgeError, "unsafe or non-writable"):
            vforge.prepare_rootless_overlay_upper(upper)

    def test_rootless_var_bind_rejects_preexisting_symlink(self) -> None:
        path = vforge.create("backend")
        parent = self.root / "external-data"
        parent.mkdir()
        (path / "var").symlink_to(parent, target_is_directory=True)
        runtime = self.root / "runtime"
        runtime.mkdir(mode=0o700)
        with (
            patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(runtime)}),
            patch.object(vforge, "verify_vforge_base_mount"),
        ):
            with self.assertRaisesRegex(vforge.VforgeError, "real directory"):
                vforge.run_environment(path, ["/bin/true"])

    def test_bind_mount_pins_regular_files_and_rejects_symlinks(self) -> None:
        source = self.root / "device"
        source.write_text("device fixture")
        target = self.root / "target"
        target.touch()
        mountpoints: list[str] = []
        with patch.object(vforge, "run") as run:
            vforge.mount_in_namespace(str(source), str(target), mountpoints=mountpoints)

        self.assertEqual(mountpoints, [str(target)])
        self.assertEqual(run.call_args.args[0][1], "--bind")
        self.assertTrue(run.call_args.args[0][2].startswith("/proc/self/fd/"))
        descriptor = int(run.call_args.args[0][2].rsplit("/", 1)[1])
        self.assertEqual(run.call_args.kwargs["pass_fds"], (descriptor,))

        link = self.root / "device-link"
        link.symlink_to(source)
        with self.assertRaisesRegex(vforge.VforgeError, "symlink"):
            vforge.mount_in_namespace(str(link), str(target))

    def test_pinned_descriptor_reaches_subprocess(self) -> None:
        # This is the exact failure the real QEMU guest hit: mount(8)
        # cannot resolve /proc/self/fd/N when Popen closes N at exec.
        source = self.root / "sealed-source"
        source.write_text("vforge-pinned-test\n", encoding="ascii")
        import os
        import sys
        descriptor = os.open(str(source), os.O_RDONLY | os.O_CLOEXEC)
        try:
            result = vforge.run(
                [sys.executable, "-c",
                 "import os,sys; print(os.readlink('/proc/self/fd/' + sys.argv[1]))",
                 str(descriptor)],
                pass_fds=(descriptor,),
            )
            self.assertEqual(result.stdout.strip(), str(source))
        finally:
            os.close(descriptor)

    def test_storage_root_can_be_selected_and_reused(self) -> None:
        selected = self.root / "persistent-vforge"
        vforge.main(["create", "--storage-root", str(selected), "backend"])

        self.assertEqual(vforge.store_root(), selected)
        self.assertTrue((selected / "environments" / "backend" / "upper").is_dir())


if __name__ == "__main__":
    unittest.main()
