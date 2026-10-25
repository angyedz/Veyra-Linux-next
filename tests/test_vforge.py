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

    def test_size_units_convert_to_exact_byte_caps(self) -> None:
        self.assertEqual(vforge.parse_size_bytes("20GiB"), 20 * 1024**3)
        self.assertEqual(vforge.parse_size_bytes("2GB"), 2_000_000_000)
        self.assertIsNone(vforge.parse_size_bytes(None))
        for invalid in ("0GiB", "20Gi", "-1GiB", "2.5GB", "20 GiB"):
            with self.subTest(invalid=invalid), self.assertRaises(vforge.VforgeError):
                vforge.parse_size_bytes(invalid)

    def test_size_capped_store_uses_sparse_ext4_image_and_fuse2fs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "environment.img"
            mount = root / "mounted"
            result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            with patch.object(vforge.shutil, "which", side_effect=lambda executable: f"/usr/bin/{executable}"), patch.object(
                vforge.subprocess, "run", return_value=result
            ) as run, patch.object(vforge.os.path, "ismount", side_effect=lambda _: run.call_count >= 2), patch.object(
                vforge.os, "chown"
            ) as chown, patch.object(vforge.os, "chmod") as chmod:
                vforge.create_size_capped_store(image, mount, 8 * 1024**3)

            self.assertEqual(image.stat().st_size, 8 * 1024**3)
            self.assertLess(image.stat().st_blocks * 512, image.stat().st_size)
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[0][:6], ["/usr/bin/mkfs.ext4", "-F", "-t", "ext4", "-m", "0"])
            self.assertEqual(run.call_args_list[1].args[0], ["/usr/bin/fuse2fs", "-o", "fakeroot", str(image), str(mount)])
            chown.assert_called_once_with(mount, os.geteuid(), os.getegid())
            chmod.assert_called_once_with(mount, 0o700)

    def test_size_capped_store_mounts_existing_ext4_without_reformatting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "environment.img"
            with image.open("wb") as stream:
                stream.truncate(4 * 1024**3)
            fd = os.open(image, os.O_WRONLY)
            try:
                os.pwrite(fd, b"\x53\xef", 1080)
            finally:
                os.close(fd)
            mount = root / "mounted"
            result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            with patch.object(vforge.shutil, "which", side_effect=lambda executable: f"/usr/bin/{executable}"), patch.object(
                vforge.subprocess, "run", return_value=result
            ) as run, patch.object(vforge.os.path, "ismount", side_effect=lambda _: run.call_count >= 1):
                vforge.create_size_capped_store(image, mount, 4 * 1024**3)

            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][0], "/usr/bin/fuse2fs")

    def test_size_capped_store_rejects_an_existing_non_ext4_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "environment.img"
            image.write_bytes(b"not an ext4 filesystem")
            image_size = image.stat().st_size
            with patch.object(vforge.shutil, "which", side_effect=lambda executable: f"/usr/bin/{executable}"), patch.object(
                vforge.subprocess, "run"
            ) as run, self.assertRaisesRegex(vforge.VforgeError, "not a valid ext filesystem"):
                vforge.create_size_capped_store(image, root / "mounted", image_size)

            run.assert_not_called()

    def test_delete_size_capped_store_unmounts_before_removing_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "environment.img"
            image.write_bytes(b"filesystem")
            mountpoint = root / "mounted"
            mountpoint.mkdir()
            (mountpoint / "data").mkdir()
            (mountpoint / "data" / "state").write_text("persistent", encoding="utf-8")
            mounted = iter((True, False, False))
            result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
            with patch.object(vforge.shutil, "which", return_value="/usr/bin/fusermount3"), patch.object(
                vforge.subprocess, "run", return_value=result
            ) as run, patch.object(vforge.os.path, "ismount", side_effect=lambda _: next(mounted)):
                vforge.remove_size_capped_store(image, mountpoint, discard=True)

            self.assertEqual(run.call_args.args[0], ["/usr/bin/fusermount3", "-u", str(mountpoint)])
            self.assertFalse(image.exists())
            self.assertFalse(mountpoint.exists())

    def test_explicit_size_limit_provisions_capped_store(self) -> None:
        with patch.object(vforge, "exists", return_value=False), patch.object(
            vforge, "create_size_capped_store"
        ) as create_store, patch.object(vforge, "host_runtime_id", return_value="runtime-test"), patch.object(
            vforge, "podman"
        ) as podman_call:
            vforge.create("backend", "none", isolation="strict", size="8GiB")

        create_store.assert_called_once()
        self.assertEqual(create_store.call_args.args[2], 8 * 1024**3)
        self.assertIn("/environments/backend/store/containers", str(podman_call.call_args.kwargs["root"]))

    def test_project_size_cap_stores_runtime_state_in_project_store(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / ".vforge.toml").write_text(
                '[environment]\nname = "backend"\nisolation = "strict"\nsize = "3GiB"\n',
                encoding="utf-8",
            )
            with patch.object(vforge, "approve_project"), patch.object(vforge, "ignore_project_state"), patch.object(
                vforge, "create_size_capped_store"
            ) as create_store, patch.object(vforge, "env_state", return_value={}), patch.object(
                vforge, "exists", return_value=False
            ), patch.object(vforge, "host_runtime_id", return_value="runtime-test"), patch.object(
                vforge, "create_container"
            ) as create_container, patch.object(vforge, "save_environment") as save, patch.object(
                vforge, "podman"
            ):
                vforge.project_up(project)

            self.assertEqual(create_store.call_args.args[2], 3 * 1024**3)
            metadata = save.call_args.args[1]
            self.assertEqual(metadata["data_root"], str(project / ".vforge" / "store"))
            self.assertEqual(metadata["podman_root"], str(project / ".vforge" / "store" / "containers"))
            self.assertEqual(create_container.call_args.kwargs["podman_root"], metadata["podman_root"])

    def test_environment_metadata_rejects_size_image_path_escape(self) -> None:
        with patch.object(vforge, "read_store_state", return_value={
            "version": vforge.STATE_VERSION,
            "environments": {
                "backend": {
                    "type": "named", "isolation": "strict", "size_bytes": 1024,
                    "size_image": "/tmp/attacker.img", "data_root": "/tmp/store",
                    "podman_root": "/tmp/store/containers",
                }
            },
        }), patch.object(vforge, "read_state", return_value={"version": vforge.STATE_VERSION, "projects": {}}), patch.object(
            vforge, "common_store_root", return_value=Path("/tmp/vforge")
        ):
            with self.assertRaisesRegex(vforge.VforgeError, "outside its Vforge storage root|unsafe size-cap image path"):
                vforge.env_state()

    def test_size_cap_metadata_uses_one_per_environment_podman_root(self) -> None:
        store = Path("/tmp/vforge/environments/backend/store")
        with patch.object(vforge, "read_store_state", return_value={
            "version": vforge.STATE_VERSION,
            "environments": {
                "backend": {
                    "type": "named", "isolation": "strict", "size_bytes": 8 * 1024**3,
                    "size_image": "/tmp/vforge/environments/backend/.vforge-size.img",
                    "data_root": str(store), "podman_root": str(store / "containers"),
                }
            },
        }), patch.object(vforge, "read_state", return_value={"version": vforge.STATE_VERSION, "projects": {}}), patch.object(
            vforge, "common_store_root", return_value=Path("/tmp/vforge")
        ):
            metadata = vforge.env_state()["backend"]

        self.assertEqual(metadata["podman_root"], "/tmp/vforge/environments/backend/store/containers")

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

    def test_integrated_snapshot_journals_and_rotates_frozen_uppers_instead_of_copying(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            layer_id = "d" * 32
            active = vforge.layer_paths(layer_id, data_root)
            (active["upper"] / "system-change").write_text("kept", encoding="utf-8")
            (active["rpmdb-upper"] / "package-state").write_text("kept", encoding="utf-8")
            metadata = {
                "type": "named", "isolation": "integrated", "layer_id": layer_id,
                "layer_stack": [], "data_root": str(data_root), "podman_root": str(data_root / "containers"),
            }
            container = {"State": {"Status": "running"}}
            observed: list[dict[str, object]] = []
            recover_function = vforge.recover_snapshot_transaction

            def recover(name: str, recorded: dict[str, object]) -> None:
                journal = vforge.read_private_json(data_root / ".snapshot-transaction.json", "test transaction")
                observed.append(journal)
                recover_function(name, recorded)

            with patch.object(vforge, "require_environment", return_value=("vforge-backend", container)), \
                 patch.object(vforge, "env_state", return_value={"backend": metadata}), \
                 patch.object(vforge, "snapshots_state", return_value={}), \
                 patch.object(vforge, "host_runtime_id", return_value="runtime-test"), \
                 patch.object(vforge, "unmount_veyra_overlay"), \
                 patch.object(vforge, "sync_storage"), \
                 patch.object(vforge, "podman", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                 patch.object(vforge, "inspect", return_value={"State": {"Status": "exited"}}), \
                 patch.object(vforge, "save_environment"), \
                 patch.object(vforge, "save_snapshots", side_effect=lambda name, entries: None), \
                 patch.object(vforge, "ensure_veyra_overlay"), \
                 patch.object(vforge, "recover_snapshot_transaction", side_effect=recover):
                vforge.make_snapshot("backend", "frozen-state")

            snapshot_root = data_root / "snapshots" / "backend" / "frozen-state"
            self.assertEqual((snapshot_root / "usr-upper/system-change").read_text(encoding="utf-8"), "kept")
            self.assertEqual((snapshot_root / "rpmdb-upper/package-state").read_text(encoding="utf-8"), "kept")
            self.assertEqual(observed[0]["entry"]["environment"]["layer_stack"], ["frozen-state"])
            self.assertEqual(list(active["upper"].iterdir()), [])
            self.assertFalse((data_root / ".snapshot-transaction.json").exists())

    def test_snapshot_overlay_layers_use_newest_frozen_upper_before_veyra_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            env_snapshots = data_root / "snapshots" / "backend"
            for snapshot in ("base-state", "later-state"):
                (env_snapshots / snapshot / "usr-upper").mkdir(parents=True)
            lower = vforge.snapshot_lowerdirs(Path("/usr"), data_root, "backend", ["base-state", "later-state"], "usr-upper")

        self.assertEqual(lower, [
            env_snapshots / "later-state" / "usr-upper",
            env_snapshots / "base-state" / "usr-upper",
            Path("/usr"),
        ])

    def test_snapshot_overlay_layers_reject_symlinked_frozen_upper(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            data_root = Path(directory)
            env_snapshots = data_root / "snapshots" / "backend"
            env_snapshots.mkdir(parents=True)
            (env_snapshots / "frozen").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(vforge.VforgeError, "snapshot layer is unsafe"):
                vforge.snapshot_lowerdirs(Path("/usr"), data_root, "backend", ["frozen"], "usr-upper")

    def test_snapshot_rotation_recovery_completes_an_interrupted_upper_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            active = vforge.layer_paths("a" * 32, data_root)
            (active["upper"] / "from-system").write_text("preserve me", encoding="utf-8")
            (active["rpmdb-upper"] / "rpm-state").write_text("rpm state", encoding="utf-8")
            staging_name = ".pending-" + "c" * 32
            stage = data_root / "snapshots" / "backend" / staging_name
            (stage / "usr-upper").parent.mkdir(parents=True)
            active["upper"].rename(stage / "usr-upper")
            active["upper"].mkdir()

            final = vforge.finish_snapshot_layer_rotation(
                "backend", "a" * 32, data_root, "before-test", staging_name, [],
            )

            self.assertEqual(final, ["before-test"])
            frozen = data_root / "snapshots" / "backend" / "before-test"
            self.assertEqual((frozen / "usr-upper/from-system").read_text(encoding="utf-8"), "preserve me")
            self.assertEqual((frozen / "rpmdb-upper/rpm-state").read_text(encoding="utf-8"), "rpm state")
            self.assertEqual(list(active["upper"].iterdir()), [])
            self.assertEqual(list(active["rpmdb-upper"].iterdir()), [])

    def test_snapshot_rotation_recovery_is_idempotent_after_final_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            active = vforge.layer_paths("b" * 32, data_root)
            staging_name = ".pending-" + "d" * 32
            stage = data_root / "snapshots" / "backend" / staging_name
            stage.mkdir(parents=True)
            (stage / "usr-upper").mkdir()
            (stage / "rpmdb-upper").mkdir()
            final_root = stage.parent / "before-test"
            stage.rename(final_root)

            first = vforge.finish_snapshot_layer_rotation(
                "backend", "b" * 32, data_root, "before-test", staging_name, [],
            )
            second = vforge.finish_snapshot_layer_rotation(
                "backend", "b" * 32, data_root, "before-test", staging_name, [],
            )

            self.assertEqual(first, ["before-test"])
            self.assertEqual(second, first)
            self.assertTrue((final_root / "usr-upper").is_dir())

    def test_snapshot_rotation_fails_closed_on_cross_mount_rename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            active = vforge.layer_paths("a" * 32, data_root)
            original = active["upper"] / "preserve"
            original.write_text("safe", encoding="utf-8")
            with patch.object(vforge.os, "rename", side_effect=OSError(vforge.errno.EXDEV, "cross-device link")):
                with self.assertRaisesRegex(vforge.VforgeError, "same filesystem mount"):
                    vforge.finish_snapshot_layer_rotation(
                        "backend", "a" * 32, data_root, "before-test",
                        ".pending-" + "e" * 32, [],
                    )
            self.assertEqual(original.read_text(encoding="utf-8"), "safe")

    def test_snapshot_rotation_rejects_a_symlinked_final_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as outside:
            data_root = Path(directory)
            vforge.layer_paths("f" * 32, data_root)
            root = data_root / "snapshots" / "backend"
            root.mkdir(parents=True)
            (root / "existing").symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(vforge.VforgeError, "may not be symlinks"):
                vforge.finish_snapshot_layer_rotation(
                    "backend", "f" * 32, data_root, "existing",
                    ".pending-" + "a" * 32, [],
                )

    def test_snapshot_journal_recovery_finishes_index_and_metadata_after_upper_rotation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            layer_id = "e" * 32
            active = vforge.layer_paths(layer_id, data_root)
            (active["upper"] / "changed-file").write_text("new state", encoding="utf-8")
            (active["rpmdb-upper"] / "rpm-state").write_text("new rpm state", encoding="utf-8")
            snapshot = "after-update"
            staging = ".pending-" + "f" * 32
            metadata = {
                "type": "named", "isolation": "integrated", "layer_id": layer_id,
                "layer_stack": [], "data_root": str(data_root), "podman_root": str(data_root / "containers"),
            }
            entry = {
                "name": snapshot, "image": vforge.snapshot_image("backend", snapshot),
                "upper": str(data_root / "snapshots" / "backend" / snapshot),
                "environment": metadata.copy(), "created": "test-time",
            }
            journal = {
                "version": vforge.STATE_VERSION, "name": "backend", "data_root": str(data_root),
                "snapshot": snapshot, "layer_id": layer_id, "staging_name": staging,
                "before_stack": [], "entry": entry, "was_running": False,
            }
            journal_path = data_root / ".snapshot-transaction.json"
            vforge.write_json_file(journal_path, journal)

            snapshots = {"backend": []}
            saved_metadata: list[dict[str, object]] = []
            with patch.object(vforge, "podman", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                 patch.object(vforge, "save_environment", side_effect=lambda name, value: saved_metadata.append(value.copy())), \
                 patch.object(vforge, "snapshots_state", side_effect=lambda: snapshots), \
                 patch.object(vforge, "save_snapshots", side_effect=lambda name, value: snapshots.__setitem__(name, value)), \
                 patch.object(vforge, "ensure_veyra_overlay"), \
                 patch.object(vforge, "inspect", return_value={"State": {"Status": "exited"}}):
                vforge.recover_snapshot_transaction("backend", metadata)

            self.assertEqual(saved_metadata[-1]["layer_stack"], [snapshot])
            self.assertEqual([item["name"] for item in snapshots["backend"]], [snapshot])
            self.assertEqual((data_root / "snapshots" / "backend" / snapshot / "usr-upper/changed-file").read_text(encoding="utf-8"), "new state")
            self.assertFalse(journal_path.exists())

    def test_snapshot_journal_is_retained_when_snapshot_image_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            layer_id = "a" * 32
            active = vforge.layer_paths(layer_id, data_root)
            (active["upper"] / "changed-file").write_text("new state", encoding="utf-8")
            (active["rpmdb-upper"] / "rpm-state").write_text("new rpm state", encoding="utf-8")
            snapshot = "after-update"
            metadata = {
                "isolation": "integrated", "layer_id": layer_id, "layer_stack": [],
                "data_root": str(data_root), "podman_root": str(data_root / "containers"),
            }
            journal_path = data_root / ".snapshot-transaction.json"
            vforge.write_json_file(journal_path, {
                "version": vforge.STATE_VERSION, "name": "backend", "data_root": str(data_root),
                "snapshot": snapshot, "layer_id": layer_id, "staging_name": ".pending-" + "b" * 32,
                "before_stack": [], "entry": {"name": snapshot, "image": vforge.snapshot_image("backend", snapshot),
                "upper": str(data_root / "snapshots" / "backend" / snapshot)}, "was_running": False,
            })
            result = subprocess.CompletedProcess([], 1, "", "")
            with patch.object(vforge, "podman", return_value=result):
                with self.assertRaisesRegex(vforge.VforgeError, "snapshot image.*missing"):
                    vforge.recover_snapshot_transaction("backend", metadata)
            self.assertTrue(journal_path.exists())
            self.assertTrue((active["upper"] / "changed-file").exists())

    def test_rollback_journal_recovery_recreates_checkpoint_after_interrupted_switch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            layer_id = "c" * 32
            vforge.layer_paths(layer_id, data_root)
            checkpoint = "rollback-checkpoint"
            checkpoint_image = vforge.snapshot_image("backend", checkpoint)
            checkpoint_metadata = {
                "type": "named", "isolation": "integrated", "layer_id": layer_id,
                "layer_stack": [checkpoint], "data_root": str(data_root),
                "podman_root": str(data_root / "containers"), "image": checkpoint_image,
                "network": "none", "mounts": [], "permissions": {"home": "none"},
            }
            frozen = data_root / "snapshots" / "backend" / checkpoint
            (frozen / "usr-upper").mkdir(parents=True)
            (frozen / "rpmdb-upper").mkdir()
            journal_path = data_root / ".rollback-transaction.json"
            vforge.write_json_file(journal_path, {
                "version": vforge.STATE_VERSION, "kind": "rollback", "name": "backend",
                "data_root": str(data_root), "checkpoint": checkpoint,
                "checkpoint_image": checkpoint_image, "checkpoint_metadata": checkpoint_metadata,
                "was_running": True, "backup_name": "vforge-backend-rollback-012345",
            })
            created: list[dict[str, object]] = []
            saved: list[dict[str, object]] = []
            with patch.object(vforge, "exists", return_value=False), \
                 patch.object(vforge, "podman", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                 patch.object(vforge, "create_container", side_effect=lambda *args, **kwargs: created.append(kwargs)), \
                 patch.object(vforge, "save_environment", side_effect=lambda name, value: saved.append(value.copy())), \
                 patch.object(vforge, "ensure_veyra_overlay"), \
                 patch.object(vforge, "unmount_veyra_overlay"), \
                 patch.object(vforge, "inspect", return_value={"State": {"Status": "exited"}}):
                vforge.recover_rollback_transaction("backend", checkpoint_metadata)

            self.assertEqual(created[0]["image"], checkpoint_image)
            self.assertEqual(created[0]["layer_stack"], [checkpoint])
            self.assertEqual(saved[-1]["layer_stack"], [checkpoint])
            self.assertFalse(journal_path.exists())

    def test_rollback_selects_frozen_snapshot_stack_without_copying_upper_trees(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            layer_id = "9" * 32
            vforge.layer_paths(layer_id, data_root)
            frozen = data_root / "snapshots" / "backend" / "known-good"
            (frozen / "usr-upper").mkdir(parents=True)
            (frozen / "rpmdb-upper").mkdir()
            metadata = {
                "type": "named", "isolation": "integrated", "layer_id": layer_id,
                "layer_stack": ["current"], "data_root": str(data_root),
                "podman_root": str(data_root / "containers"), "network": "none",
                "mounts": [], "permissions": {"home": "none"},
            }
            target_metadata = metadata.copy()
            target_metadata["layer_stack"] = ["known-good"]
            target_image = vforge.snapshot_image("backend", "known-good")
            checkpoint_image = vforge.snapshot_image("backend", "rollback-point")
            snapshots = {
                "backend": [
                    {"name": "known-good", "image": target_image, "upper": str(frozen),
                     "environment": target_metadata, "host_runtime": "runtime-test"},
                    {"name": "rollback-point", "image": checkpoint_image, "upper": None,
                     "environment": {**metadata, "layer_stack": ["current", "rollback-point"]}},
                ]
            }
            created: list[dict[str, object]] = []
            with patch.object(vforge, "require_environment", return_value=("vforge-backend", {"State": {"Status": "running"}})), \
                 patch.object(vforge, "env_state", return_value={"backend": metadata}), \
                 patch.object(vforge, "snapshots_state", side_effect=lambda: snapshots), \
                 patch.object(vforge, "make_snapshot", return_value="rollback-point"), \
                 patch.object(vforge, "exists", return_value=False), \
                 patch.object(vforge, "podman", return_value=subprocess.CompletedProcess([], 0, "", "")), \
                 patch.object(vforge, "unmount_veyra_overlay"), \
                 patch.object(vforge, "ensure_veyra_overlay"), \
                 patch.object(vforge, "create_container", side_effect=lambda *args, **kwargs: created.append(kwargs)), \
                 patch.object(vforge, "save_environment"), \
                 patch.object(vforge, "host_runtime_id", return_value="runtime-test"):
                vforge.rollback_to("backend", "known-good", confirm=False)

            self.assertEqual(created[0]["layer_stack"], ["known-good"])
            self.assertEqual(created[0]["image"], target_image)
            self.assertFalse((data_root / ".rollback-transaction.json").exists())


if __name__ == "__main__":
    unittest.main()
