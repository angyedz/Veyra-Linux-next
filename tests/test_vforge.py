from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import io
import json
import os
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "mkosi.extra/usr/bin/vforge"


SPEC = importlib.util.spec_from_loader(
    "veyra_vforge", SourceFileLoader("veyra_vforge", str(SCRIPT))
)
assert SPEC and SPEC.loader
vforge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vforge)


class VforgeCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        self.env_patch = patch.dict(os.environ, {
            "XDG_STATE_HOME": str(Path(self.state.name) / "state"),
            "XDG_DATA_HOME": str(Path(self.state.name) / "data"),
            "XDG_CONFIG_HOME": str(Path(self.state.name) / "config"),
        })
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.previous_storage_root = vforge.ACTIVE_STORAGE_ROOT
        vforge.ACTIVE_STORAGE_ROOT = None
        self.addCleanup(setattr, vforge, "ACTIVE_STORAGE_ROOT", self.previous_storage_root)

    def test_environment_names_reject_path_and_shell_syntax(self) -> None:
        for name in ("../host", "backend;id", "Uppercase", "a" * 56):
            with self.subTest(name=name), self.assertRaises(vforge.VforgeError):
                vforge.container_name(name)

    def test_create_uses_fixed_image_and_network_none_without_mounts(self) -> None:
        with patch.object(vforge, "exists", return_value=False), patch.object(
            vforge, "ensure_veyra_overlay", return_value={
                "usr": Path("/tmp/vforge-test/usr"),
                "rpmdb": Path("/tmp/vforge-test/rpmdb"),
                "rpmdb-target": Path("/usr/lib/sysimage/rpm"),
            }
        ), patch.object(vforge, "host_runtime_id", return_value="runtime-test"), patch.object(
            vforge, "podman"
        ) as call:
            vforge.create("backend", "none")

        argv = call.call_args.args[0]
        self.assertEqual(argv[0:2], ["container", "create"])
        self.assertIn(vforge.FEDORA_IMAGE, argv)
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--init", argv)
        self.assertIn("--cap-drop=all", argv)
        self.assertIn("--security-opt=no-new-privileges", argv)
        self.assertIn(f"{vforge.LABEL}=1", argv)
        volumes = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--volume"]
        self.assertIn("/tmp/vforge-test/usr:/usr:rw", volumes)
        self.assertIn("/tmp/vforge-test/rpmdb:/usr/lib/sysimage/rpm:rw", volumes)
        self.assertNotIn("--privileged", argv)

    def test_create_allows_explicit_network_opt_in(self) -> None:
        with patch.object(vforge, "exists", return_value=False), patch.object(
            vforge, "ensure_veyra_overlay", return_value={
                "usr": Path("/tmp/vforge-test/usr"),
                "rpmdb": Path("/tmp/vforge-test/rpmdb"),
                "rpmdb-target": Path("/usr/lib/sysimage/rpm"),
            }
        ), patch.object(vforge, "host_runtime_id", return_value="runtime-test"), patch("builtins.input", return_value="yes"), patch.object(
            vforge.sys.stdin, "isatty", return_value=True
        ), patch.object(
            vforge, "podman"
        ) as call:
            vforge.create("backend", "bridge")

        argv = call.call_args.args[0]
        self.assertEqual(argv[argv.index("--network") + 1], "bridge")

    def test_podman_is_forced_to_use_local_connection_and_clears_remote_environment(self) -> None:
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
        with patch.dict(
            os.environ,
            {
                "CONTAINER_HOST": "tcp://example.invalid:1234",
                "CONTAINER_CONNECTION": "remote",
                "CONTAINER_SSHKEY": "/tmp/key",
                "DOCKER_HOST": "tcp://example.invalid:1234",
            },
        ), patch.object(vforge.shutil, "which", return_value="/usr/bin/podman"), patch.object(
            vforge.subprocess, "run", return_value=result
        ) as run:
            vforge.podman(["info"], capture=True)

        argv = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertEqual(argv[:2], ["/usr/bin/podman", "--remote=false"])
        self.assertIn("--root", argv)
        self.assertIn("--runroot", argv)
        for key in ("CONTAINER_HOST", "CONTAINER_CONNECTION", "CONTAINER_SSHKEY", "DOCKER_HOST"):
            self.assertNotIn(key, env)

    def test_storage_root_is_persisted_and_rejects_symlink_components(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            selected = Path(parent) / "vforge-store"
            configured = vforge.set_storage_root(str(selected))
            self.assertEqual(configured, selected.resolve())
            self.assertEqual(vforge.resolve_storage_root(), selected.resolve())

            alias = Path(parent) / "alias"
            alias.symlink_to(selected, target_is_directory=True)
            with self.assertRaisesRegex(vforge.VforgeError, "contains a symlink"):
                vforge.resolve_storage_root(str(alias / "other"))

    def test_default_storage_root_works_before_xdg_config_exists(self) -> None:
        expected = Path(os.environ["XDG_DATA_HOME"]) / "vforge"
        self.assertEqual(vforge.resolve_storage_root(), expected.resolve())

    def test_project_root_symlink_is_rejected_before_canonicalization(self) -> None:
        with tempfile.TemporaryDirectory() as parent:
            root = Path(parent)
            project = root / "project"
            project.mkdir()
            alias = root / "project-alias"
            alias.symlink_to(project, target_is_directory=True)
            with self.assertRaisesRegex(vforge.VforgeError, "project root contains a symlink"):
                vforge.canonical_project_root(alias)

    def test_project_config_cannot_mount_its_private_state_directory(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            state = project / ".vforge"
            state.mkdir()
            (state / "secrets.json").write_text("{}", encoding="utf-8")
            (project / vforge.PROJECT_CONFIG).write_text(
                f'''[environment]\nname = "backend"\n\n[[permissions.mounts]]\nsource = "{state}"\ntarget = "/workspace/state"\naccess = "read-only"\n''',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(vforge.VforgeError, "mounting project-local .vforge state is forbidden"):
                vforge.parse_project_config(project)

    def test_project_mount_target_cannot_follow_project_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir, tempfile.TemporaryDirectory() as source_dir:
            project = Path(project_dir)
            (project / ".vforge").mkdir()
            (project / "state-link").symlink_to(project / ".vforge", target_is_directory=True)
            (project / vforge.PROJECT_CONFIG).write_text(
                f'''[environment]\nname = "backend"\n\n[[permissions.mounts]]\nsource = "{source_dir}"\ntarget = "/workspace/state-link/data"\naccess = "read-only"\n''',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(vforge.VforgeError, "mount target follows a symlink"):
                vforge.parse_project_config(project)

    def test_runtime_identity_includes_immutable_root_hash(self) -> None:
        identity = {"ID": "veyra", "VERSION_ID": "44"}
        first = vforge.runtime_digest(identity, "glibc=1;systemd=1", "1", "a" * 64)
        second = vforge.runtime_digest(identity, "glibc=1;systemd=1", "1", "b" * 64)
        self.assertNotEqual(first, second)
        with self.assertRaisesRegex(vforge.VforgeError, "compatibility metadata is incomplete"):
            vforge.runtime_digest(identity, "glibc=1", "1", "")

    def test_explicit_size_limit_fails_when_backend_cannot_enforce_it(self) -> None:
        with self.assertRaisesRegex(vforge.VforgeError, "size limits are unsupported"):
            vforge.create("backend", "none", isolation="strict", size="20GiB")

    def test_named_environment_rejects_project_only_home_without_project(self) -> None:
        with self.assertRaisesRegex(vforge.VforgeError, "available only for project environments"):
            vforge.create("backend", "none", isolation="integrated", home_mode="project-only")

    def test_vforge_refuses_to_run_as_root(self) -> None:
        with patch.object(vforge.os, "geteuid", return_value=0), patch(
            "sys.stderr", new_callable=io.StringIO
        ) as error:
            status = vforge.main(["list"])

        self.assertEqual(status, 1)
        self.assertIn("without sudo", error.getvalue())

    def test_enter_starts_a_stopped_environment_before_exec(self) -> None:
        metadata = {"State": {"Status": "exited"}}
        with patch.object(vforge, "require_environment", return_value=("vforge-backend", metadata)), patch.object(
            vforge, "env_state", return_value={"backend": {"isolation": "strict", "host_runtime": "runtime-test"}}
        ), patch.object(vforge, "host_runtime_id", return_value="runtime-test"), patch.object(
            vforge, "podman", return_value=subprocess.CompletedProcess(args=[], returncode=0)
        ) as call:
            self.assertEqual(vforge.ensure_running("backend"), "vforge-backend")

        self.assertEqual(call.call_args.args[0], ["container", "start", "vforge-backend"])

    def test_list_shows_only_vforge_containers(self) -> None:
        payload = json.dumps(
            [
                {"Names": ["vforge-backend"], "State": "running", "Image": vforge.FEDORA_IMAGE},
                {"Names": ["other"], "State": "running", "Image": "fedora:44"},
            ]
        )
        result = subprocess.CompletedProcess(args=[], returncode=0, stdout=payload, stderr="")
        with patch.object(vforge, "podman", return_value=result), patch(
            "sys.stdout", new_callable=io.StringIO
        ) as output:
            vforge.list_environments()

        text = output.getvalue()
        self.assertIn("backend", text)
        self.assertNotIn("other", text)

    def test_project_config_applies_defaults_and_explicit_read_only_mount(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir, tempfile.TemporaryDirectory() as shared_dir:
            project = Path(project_dir)
            (project / vforge.PROJECT_CONFIG).write_text(
                f'''[environment]\nname = "backend"\nisolation = "integrated"\n\n[packages]\nsystem = []\n\n[permissions]\nnetwork = "shared"\n\n[permissions.home]\nmode = "project-only"\n\n[[permissions.mounts]]\nsource = "{shared_dir}"\ntarget = "/workspace/shared"\naccess = "read-only"\n''',
                encoding="utf-8",
            )
            config = vforge.parse_project_config(project)

        self.assertEqual(config["name"], "backend")
        self.assertEqual(config["permissions"]["home"], "project-only")
        self.assertEqual(config["permissions"]["mounts"][0]["access"], "read-only")
        self.assertEqual(config["permissions"]["network"], "shared")

    def test_project_config_rejects_symlinked_config_and_protected_targets(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir, tempfile.TemporaryDirectory() as outside_dir:
            project = Path(project_dir)
            config = Path(outside_dir) / "config.toml"
            shared = Path(outside_dir) / "shared"
            shared.mkdir()
            config.write_text('[environment]\nname="backend"\n', encoding="utf-8")
            (project / vforge.PROJECT_CONFIG).symlink_to(config)
            with self.assertRaisesRegex(vforge.VforgeError, "regular file"):
                vforge.parse_project_config(project)

            (project / vforge.PROJECT_CONFIG).unlink()
            (project / vforge.PROJECT_CONFIG).write_text(
                f'[environment]\nname="backend"\n[permissions]\n[[permissions.mounts]]\nsource="{shared}"\ntarget="/workspace"\naccess="read-only"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(vforge.VforgeError, "target is unsafe or protected"):
                vforge.parse_project_config(project)

            (project / vforge.PROJECT_CONFIG).unlink()
            (project / vforge.PROJECT_CONFIG).write_text(
                '[environment]\nname="backend"\n[permissions]\n[[permissions.mounts]]\nsource="/etc"\ntarget="/etc"\naccess="read-write"\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(vforge.VforgeError, "protected host path"):
                vforge.parse_project_config(project)

    def test_strict_profile_rejects_host_mounts_and_graphics_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir, tempfile.TemporaryDirectory() as shared_dir:
            project = Path(project_dir)
            (project / vforge.PROJECT_CONFIG).write_text(
                '[environment]\nname="strict-env"\nisolation="strict"\n[permissions]\nnetwork="none"\nwayland=true\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(vforge.VforgeError, "strict isolation"):
                vforge.parse_project_config(project)
            (project / vforge.PROJECT_CONFIG).write_text(
                '[environment]\nname="strict-env"\nisolation="strict"\n[permissions]\nnetwork="none"\n',
                encoding="utf-8",
            )
            config = vforge.parse_project_config(project)
            self.assertEqual(config["permissions"]["home"], "none")

    def test_project_approval_is_stored_outside_checkout_and_expansion_prompts_again(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / vforge.PROJECT_CONFIG).write_text('[environment]\nname="backend"\n', encoding="utf-8")
            first = vforge.parse_project_config(project)
            with patch.object(vforge.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="yes") as prompt:
                vforge.approve_project(first, project)
                vforge.approve_project(first, project)
            self.assertEqual(prompt.call_count, 1)

            (project / vforge.PROJECT_CONFIG).write_text(
                '[environment]\nname="backend"\n[permissions]\nnetwork="shared"\n', encoding="utf-8"
            )
            expanded = vforge.parse_project_config(project)
            with patch.object(vforge.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="yes") as prompt:
                vforge.approve_project(expanded, project)
            prompt.assert_called_once()

    def test_snapshot_commits_persistent_container_state(self) -> None:
        metadata = {"State": {"Status": "running"}}
        record = {"type": "named", "isolation": "strict", "host_runtime": "runtime-test"}
        with patch.object(vforge, "require_environment", return_value=("vforge-backend", metadata)), patch.object(
            vforge, "env_state", return_value={"backend": record}
        ), patch.object(vforge, "snapshots_state", return_value={}), patch.object(
            vforge, "host_runtime_id", return_value="runtime-test"
        ), patch.object(vforge, "save_snapshots") as save, patch.object(vforge, "podman") as call:
            snapshot = vforge.make_snapshot("backend", "before-test")

        self.assertEqual(snapshot, "before-test")
        commands = [invocation.args[0] for invocation in call.call_args_list]
        self.assertIn(["container", "stop", "--time", "10", "vforge-backend"], commands)
        self.assertIn(["container", "commit", "--pause=true", "vforge-backend", "localhost/vforge-snapshot:backend-before-test"], commands)
        self.assertEqual(commands[-1], ["container", "start", "vforge-backend"])
        self.assertEqual(save.call_args.args[1][0]["environment"], record)


if __name__ == "__main__":
    unittest.main()
