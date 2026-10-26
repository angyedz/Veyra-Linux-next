from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import ANY, patch


SCRIPT = Path(__file__).resolve().parents[1] / "mkosi.extra/usr/bin/vforge"


SPEC = importlib.util.spec_from_loader(
    "veyra_vforge", SourceFileLoader("veyra_vforge", str(SCRIPT))
)
assert SPEC and SPEC.loader
vforge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vforge)


class VforgeCommandTests(unittest.TestCase):
    BASE_IDENTITY = {
        "image_id": "veyra-next", "image_version": "0.1.0",
        "base_digest": "a" * 64,
        "image": "localhost/veyra-vforge-base:" + "a" * 64,
    }

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

    def test_veyra_base_identity_requires_fedora44_private_root_and_vpkg(self) -> None:
        labels = {
            "org.veyra.base.id": "veyra-next",
            "org.veyra.base.version": "0.1.0",
            "org.veyra.base.schema": "1",
        }
        release = {"ID": "fedora", "VERSION_ID": "44", "IMAGE_ID": "veyra-next", "IMAGE_VERSION": "0.1.0"}
        identity = vforge.validate_veyra_base_identity(
            labels, release, "sha256:" + "a" * 64, rpmdb_private=True, vpkg_container=True
        )
        self.assertEqual(identity["image_version"], "0.1.0")
        self.assertEqual(identity["base_digest"], "a" * 64)

        invalid = (
            ({**labels, "org.veyra.base.id": "fedora"}, release, "sha256:" + "a" * 64, True, True),
            (labels, {**release, "ID": "ubuntu"}, "sha256:" + "a" * 64, True, True),
            (labels, {**release, "IMAGE_VERSION": "0.2.0"}, "sha256:" + "a" * 64, True, True),
            (labels, release, "sha256:bad", True, True),
            (labels, release, "sha256:" + "a" * 64, False, True),
            (labels, release, "sha256:" + "a" * 64, True, False),
        )
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(vforge.VforgeError):
                vforge.validate_veyra_base_identity(
                    candidate[0], candidate[1], candidate[2],
                    rpmdb_private=candidate[3], vpkg_container=candidate[4],
                )

    def test_create_fails_before_environment_state_changes_without_imported_veyra_base(self) -> None:
        with patch.object(vforge, "require_imported_veyra_base", side_effect=vforge.VforgeError("missing Veyra OCI base")) as require_base, \
             patch.object(vforge, "env_state") as env_state, \
             patch.object(vforge, "configure_environment_store") as configure:
            with self.assertRaisesRegex(vforge.VforgeError, "missing Veyra OCI base"):
                vforge.create("backend", "none")

        require_base.assert_called_once()
        env_state.assert_not_called()
        configure.assert_not_called()

    def test_root_marker_matches_vpkg_container_contract_and_is_mounted_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            metadata = {
                "root_mode": "veyra-oci", "image": self.BASE_IDENTITY["image"],
                "base_image": self.BASE_IDENTITY["image"], "image_id": "veyra-next",
                "image_version": "0.1.0", "base_digest": "a" * 64,
                "root_id": "b" * 32, "data_root": str(data_root),
            }
            marker = vforge.write_container_root_marker(data_root, "b" * 32, self.BASE_IDENTITY)
            self.assertEqual(marker.name, "container-root.json")
            self.assertEqual(marker.stat().st_mode & 0o777, 0o444)
            self.assertEqual(json.loads(marker.read_text(encoding="utf-8")), {
                "schema": 1, "kind": "vforge-veyra-root", "root_id": "b" * 32,
                "image_id": "veyra-next", "image_version": "0.1.0",
                "base_digest": "a" * 64, "storage_driver": "podman-overlay",
                "immutable_base": True,
            })
            vforge.validate_container_root_marker(metadata)
            vforge.validate_container_mounts({"Mounts": [
                {"Destination": "/run/veyra-vforge/root.json", "RW": False},
            ]})
            with self.assertRaisesRegex(vforge.VforgeError, "must remain private"):
                vforge.validate_container_mounts({"Mounts": [
                    {"Destination": "/usr", "RW": True},
                    {"Destination": "/run/veyra-vforge/root.json", "RW": False},
                ]})

    def test_foreign_owned_base_archive_requires_a_read_only_mount(self) -> None:
        ro = subprocess.CompletedProcess([], 0, "ro,relatime,trans=9p", "")
        rw = subprocess.CompletedProcess([], 0, "rw,relatime,trans=9p", "")
        with patch.object(vforge.subprocess, "run", return_value=ro):
            self.assertTrue(vforge.archive_is_on_read_only_mount(Path("/run/vforge-base/base.tar")))
        with patch.object(vforge.subprocess, "run", return_value=rw):
            self.assertFalse(vforge.archive_is_on_read_only_mount(Path("/tmp/base.tar")))
        with patch.object(vforge.subprocess, "run", side_effect=OSError("missing findmnt")):
            self.assertFalse(vforge.archive_is_on_read_only_mount(Path("/tmp/base.tar")))

    def test_create_uses_fixed_image_and_network_none_without_mounts(self) -> None:
        with patch.object(vforge, "exists", return_value=False), \
             patch.object(vforge, "require_imported_veyra_base", return_value=self.BASE_IDENTITY), \
             patch.object(vforge, "ensure_veyra_base_in_store", return_value=self.BASE_IDENTITY), \
             patch.object(vforge, "verify_podman_overlay"), patch.object(vforge, "podman") as call:
            vforge.create("backend", "none")

        argv = call.call_args.args[0]
        self.assertEqual(argv[0:2], ["container", "create"])
        self.assertIn(self.BASE_IDENTITY["image"], argv)
        self.assertEqual(argv[argv.index("--network") + 1], "none")
        self.assertIn("--init", argv)
        self.assertIn("--cap-drop=all", argv)
        for capability in vforge.VPKG_CONTAINER_CAPABILITIES:
            self.assertIn(f"--cap-add={capability}", argv)
        self.assertIn("--security-opt=no-new-privileges", argv)
        self.assertIn(f"{vforge.LABEL}=1", argv)
        volumes = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--volume"]
        self.assertTrue(any(value.endswith(":/run/veyra-vforge/root.json:ro") for value in volumes))
        self.assertFalse(any(":/usr:" in value or ":/usr/lib/sysimage/rpm:" in value for value in volumes))
        self.assertNotIn("--privileged", argv)

    def test_create_allows_explicit_network_opt_in(self) -> None:
        with patch.object(vforge, "exists", return_value=False), \
             patch.object(vforge, "require_imported_veyra_base", return_value=self.BASE_IDENTITY), \
             patch.object(vforge, "ensure_veyra_base_in_store", return_value=self.BASE_IDENTITY), \
             patch.object(vforge, "verify_podman_overlay"), patch("builtins.input", return_value="yes"), patch.object(
            vforge.sys.stdin, "isatty", return_value=True
        ), patch.object(
            vforge, "podman"
        ) as call:
            vforge.create("backend", "bridge")

        argv = call.call_args.args[0]
        self.assertEqual(argv[argv.index("--network") + 1], "bridge")

    def test_named_network_permission_fails_closed_without_interactive_approval(self) -> None:
        with patch.object(vforge.sys.stdin, "isatty", return_value=False), patch.object(
            vforge, "podman"
        ) as podman, patch.object(vforge, "require_imported_veyra_base") as base:
            with self.assertRaisesRegex(vforge.VforgeError, "interactive approval"):
                vforge.create("backend", "bridge")

        podman.assert_not_called()
        base.assert_not_called()

    def test_named_graphics_permission_requires_trusted_profile_and_interactive_consent(self) -> None:
        with patch.object(vforge.sys.stdin, "isatty", return_value=False), \
             patch.object(vforge, "host_runtime_id") as runtime, \
             patch.object(vforge, "podman") as podman:
            with self.assertRaisesRegex(vforge.VforgeError, "require --isolation trusted"):
                vforge.create("backend", "none", wayland=True)
            with self.assertRaisesRegex(vforge.VforgeError, "interactive approval"):
                vforge.create("backend", "none", isolation="trusted", wayland=True)

        runtime.assert_not_called()
        podman.assert_not_called()

    def test_named_home_permission_requires_interactive_consent(self) -> None:
        with patch.object(vforge.sys.stdin, "isatty", return_value=False), \
             patch.object(vforge, "host_runtime_id") as runtime, \
             patch.object(vforge, "podman") as podman:
            with self.assertRaisesRegex(vforge.VforgeError, "interactive approval"):
                vforge.create("backend", "none", home_mode="read-only")

        runtime.assert_not_called()
        podman.assert_not_called()

    def test_home_permissions_map_only_the_selected_host_home_access(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary) / "home"
            home.mkdir()
            expected_home = f"{home.resolve()}:/home/host:ro"
            for mode, expected in (("none", None), ("read-only", expected_home),
                                   ("read-write", f"{home.resolve()}:/home/host:rw")):
                with self.subTest(mode=mode), patch.object(vforge, "exists", return_value=False), \
                     patch.object(vforge, "ensure_veyra_overlay", return_value={
                         "usr": Path("/tmp/vforge-test/usr"),
                         "rpmdb": Path("/tmp/vforge-test/rpmdb"),
                         "rpmdb-target": Path("/usr/lib/sysimage/rpm"),
                     }), patch.object(vforge, "podman") as podman, \
                     patch.object(Path, "home", return_value=home):
                    with patch.object(vforge, "verify_podman_overlay"):
                        vforge.create_container(
                            "backend", "none", layer_id="a" * 32,
                            resources={"home": mode}, root_id="b" * 32,
                            base_identity=self.BASE_IDENTITY, podman_root=Path(self.state.name) / "containers",
                        )
                argv = podman.call_args.args[0]
                volumes = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--volume"]
                if expected is None:
                    self.assertFalse(any(value.startswith(f"{home.resolve()}:") for value in volumes))
                else:
                    self.assertIn(expected, volumes)

    def test_project_only_does_not_mount_the_whole_host_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            project = Path(temporary) / "project"
            project.mkdir()
            with patch.object(vforge, "exists", return_value=False), patch.object(
                vforge, "verify_podman_overlay"
            ), patch.object(vforge, "podman") as podman:
                vforge.create_container(
                    "backend", "none", layer_id="a" * 32,
                    mounts=[{"source": str(project), "target": "/workspace", "access": "read-write"}],
                    resources={"home": "project-only", "project": str(project)},
                    root_id="b" * 32, base_identity=self.BASE_IDENTITY,
                    podman_root=Path(self.state.name) / "containers",
                )

            argv = podman.call_args.args[0]
            volumes = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--volume"]
            self.assertIn(f"{project.resolve()}:/workspace:rw", volumes)
            self.assertFalse(any(value.endswith(":/home/host:ro") or value.endswith(":/home/host:rw") for value in volumes))

    def test_trusted_host_resources_bind_only_approved_user_sockets_and_gpu_devices(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runtime = Path(temporary) / "runtime"
            runtime.mkdir()
            wayland_path = runtime / "wayland-0"
            pipewire_path = runtime / "pipewire-0"
            bus_path = runtime / "bus"
            sockets = []
            for path in (wayland_path, pipewire_path, bus_path):
                channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                channel.bind(str(path))
                sockets.append(channel)
            self.addCleanup(lambda: [channel.close() for channel in sockets])
            device = Path("/dev/dri/renderD128")
            original_glob = Path.glob

            def devices(path: Path, pattern: str):
                if path == Path("/dev/dri"):
                    return iter([device])
                return original_glob(path, pattern)

            with patch.dict(os.environ, {
                "XDG_RUNTIME_DIR": str(runtime),
                "WAYLAND_DISPLAY": "wayland-0",
                "DBUS_SESSION_BUS_ADDRESS": f"unix:path={bus_path}",
            }), patch.object(Path, "glob", devices), patch.object(Path, "is_char_device", return_value=True), \
                 patch.object(vforge, "exists", return_value=False), patch.object(
                    vforge, "ensure_veyra_overlay", return_value={
                        "usr": Path("/tmp/vforge-test/usr"),
                        "rpmdb": Path("/tmp/vforge-test/rpmdb"),
                        "rpmdb-target": Path("/usr/lib/sysimage/rpm"),
                    }
                 ), patch.object(vforge, "verify_podman_overlay"), patch.object(vforge, "podman") as podman:
                vforge.create_container(
                    "backend", "none", isolation="trusted", layer_id="a" * 32,
                    resources={"home": "none", "gpu": True, "wayland": True,
                               "audio": True, "system_bus": True},
                    root_id="b" * 32, base_identity=self.BASE_IDENTITY,
                    podman_root=Path(self.state.name) / "containers",
                )

            argv = podman.call_args.args[0]
            volumes = [argv[index + 1] for index, value in enumerate(argv[:-1]) if value == "--volume"]
            self.assertIn(f"{wayland_path}:/run/vforge/wayland.sock:rw", volumes)
            self.assertIn(f"{pipewire_path}:/run/vforge/pipewire-0:rw", volumes)
            self.assertIn(f"{bus_path}:/run/vforge/user-bus.sock:rw", volumes)
            self.assertNotIn("/run/dbus/system_bus_socket", " ".join(argv))
            self.assertIn("--device", argv)
            self.assertIn(str(device), argv)

    def test_create_container_rejects_trusted_resources_in_integrated_profile(self) -> None:
        with patch.object(vforge, "exists") as exists, patch.object(vforge, "podman") as podman:
            with self.assertRaisesRegex(vforge.VforgeError, "require isolation = 'trusted'"):
                vforge.create_container(
                    "backend", "none", isolation="integrated", layer_id="a" * 32,
                    resources={"home": "none", "gpu": True},
                )

        exists.assert_not_called()
        podman.assert_not_called()

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
        self.assertNotEqual(
            first, vforge.runtime_digest(identity, "glibc=2;systemd=1", "1", "a" * 64)
        )
        self.assertNotEqual(
            first, vforge.runtime_digest(identity, "glibc=1;systemd=1", "2", "a" * 64)
        )
        with self.assertRaisesRegex(vforge.VforgeError, "compatibility metadata is incomplete"):
            vforge.runtime_digest(identity, "glibc=1", "1", "")

    def test_host_runtime_identity_reads_os_release_abi_rpm_evr_and_verity_root_hash(self) -> None:
        root_hash = "c" * 64
        contents = {
            Path("/etc/os-release"): 'ID=fedora\nVERSION_ID="44"\n',
            Path("/usr/share/veyra/sysext-level"): "1\n",
            Path("/proc/cmdline"): f"quiet roothash={root_hash}\n",
        }
        original_read_text = Path.read_text

        def read_runtime_metadata(path: Path, *args, **kwargs):
            if path in contents:
                return contents[path]
            return original_read_text(path, *args, **kwargs)

        rpm = subprocess.CompletedProcess([], 0, "glibc=2.39-1.fc44;systemd=256-1.fc44;\n", "")
        with patch.object(Path, "read_text", read_runtime_metadata), patch.object(
            vforge.subprocess, "run", return_value=rpm
        ) as run:
            result = vforge.host_runtime_id()

        run.assert_called_once_with(
            ["rpm", "-q", "--qf", "%{NAME}=%{VERSION}-%{RELEASE};", "glibc", "systemd"],
            check=True, capture_output=True, text=True,
        )
        self.assertEqual(
            result,
            vforge.runtime_digest(
                {"ID": "fedora", "VERSION_ID": "44"},
                "glibc=2.39-1.fc44;systemd=256-1.fc44;", "1", root_hash,
            ),
        )

    def test_integrated_environment_refuses_to_start_after_veyra_generation_changes(self) -> None:
        metadata = {"State": {"Status": "exited"}}
        record = {"root_mode": "fedora-overlay", "isolation": "integrated"}
        with patch.object(vforge, "require_environment", return_value=("vforge-backend", metadata)), \
             patch.object(vforge, "env_state", return_value={"backend": record}), \
             patch.object(vforge, "host_runtime_id", return_value="new-generation"), \
             patch.object(vforge, "podman") as podman:
            with self.assertRaisesRegex(vforge.VforgeError, "legacy Fedora/host-overlay"):
                vforge.ensure_running("backend")

        podman.assert_not_called()

    def test_strict_environment_can_start_after_veyra_generation_changes(self) -> None:
        metadata = {"State": {"Status": "exited"}}
        record = {"root_mode": "fedora-overlay", "isolation": "strict"}
        with patch.object(vforge, "require_environment", return_value=("vforge-backend", metadata)), \
             patch.object(vforge, "env_state", return_value={"backend": record}), \
             patch.object(vforge, "podman", return_value=subprocess.CompletedProcess([], 0, "", "")) as podman:
            with self.assertRaisesRegex(vforge.VforgeError, "legacy Fedora/host-overlay"):
                vforge.ensure_running("backend")

        podman.assert_not_called()

    def test_upgrade_rebinds_integrated_environment_to_current_veyra_generation(self) -> None:
        container = {"State": {"Status": "running"}}
        metadata = {"root_mode": "fedora-overlay", "isolation": "integrated", "network": "none"}
        result = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(vforge, "require_environment", return_value=("vforge-backend", container)), \
             patch.object(vforge, "env_state", return_value={"backend": metadata}), \
             patch.object(vforge, "host_runtime_id", return_value="new-generation"), \
             patch.object(vforge, "make_snapshot", return_value="before-upgrade") as snapshot, \
             patch.object(vforge, "podman", return_value=result) as podman, \
             patch.object(vforge, "save_environment") as save:
            with self.assertRaisesRegex(vforge.VforgeError, "legacy Fedora/host-overlay"):
                vforge.upgrade("backend")

        snapshot.assert_not_called()
        podman.assert_not_called()
        save.assert_not_called()

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
        ) as create_store, patch.object(vforge, "require_imported_veyra_base", return_value=self.BASE_IDENTITY), \
             patch.object(vforge, "ensure_veyra_base_in_store", return_value=self.BASE_IDENTITY), patch.object(vforge, "verify_podman_overlay"), patch.object(
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
            ), patch.object(vforge, "require_imported_veyra_base", return_value=self.BASE_IDENTITY), patch.object(
                vforge, "ensure_veyra_base_in_store", return_value=self.BASE_IDENTITY
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
            vforge, "env_state", return_value={"backend": {"root_mode": "fedora-overlay"}}
        ), patch.object(vforge, "podman") as call:
            with self.assertRaisesRegex(vforge.VforgeError, "legacy Fedora/host-overlay"):
                vforge.ensure_running("backend")

        call.assert_not_called()

    def test_list_shows_only_vforge_containers(self) -> None:
        payload = json.dumps(
            [
                {"Names": ["vforge-backend"], "State": "running", "Image": self.BASE_IDENTITY["image"]},
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

    def test_project_hardware_permissions_require_trusted_profile(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            for permission in ("gpu", "wayland", "audio", "system_bus"):
                with self.subTest(permission=permission):
                    (project / vforge.PROJECT_CONFIG).write_text(
                        f'[environment]\nname="backend"\nisolation="integrated"\n'
                        f'[permissions]\n{permission}=true\n',
                        encoding="utf-8",
                    )
                    with self.assertRaisesRegex(vforge.VforgeError, "require isolation = 'trusted'"):
                        vforge.parse_project_config(project)

    def test_project_config_rejects_arbitrary_device_grants(self) -> None:
        with tempfile.TemporaryDirectory() as project_dir:
            project = Path(project_dir)
            (project / vforge.PROJECT_CONFIG).write_text(
                '[environment]\nname="backend"\n[permissions]\ndevices=["/dev/kvm"]\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(vforge.VforgeError, r"unsupported \[permissions\] setting.*devices"):
                vforge.parse_project_config(project)

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

    def test_named_network_permission_fails_closed_without_interactive_approval(self) -> None:
        with patch.object(vforge.sys.stdin, "isatty", return_value=False), patch.object(
            vforge, "podman"
        ) as podman, patch.object(vforge, "ensure_veyra_overlay") as overlay:
            with self.assertRaisesRegex(vforge.VforgeError, "interactive approval"):
                vforge.create("backend", "bridge")

        podman.assert_not_called()
        overlay.assert_not_called()

    def test_snapshot_commits_persistent_container_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data_root = Path(directory)
            record = {
                "type": "named", "root_mode": "veyra-oci", "isolation": "strict",
                "image": self.BASE_IDENTITY["image"], "base_image": self.BASE_IDENTITY["image"],
                "image_id": "veyra-next", "image_version": "0.1.0",
                "base_digest": "a" * 64, "root_id": "b" * 32, "data_root": str(data_root),
            }
            vforge.write_container_root_marker(data_root, "b" * 32, self.BASE_IDENTITY)
            container = {
                "State": {"Status": "running"},
                "Config": {"Labels": {
                    vforge.LABEL: "1", "io.veyra.vforge.root-id": "b" * 32,
                    "io.veyra.vforge.base-digest": "a" * 64,
                }},
                "Mounts": [{"Destination": "/run/veyra-vforge/root.json", "RW": False}],
            }
            with patch.object(vforge, "require_environment", return_value=("vforge-backend", container)), patch.object(
                vforge, "env_state", return_value={"backend": record}
            ), patch.object(vforge, "snapshots_state", return_value={}), patch.object(
                vforge, "save_snapshots"
            ) as save, patch.object(vforge, "podman") as call:
                snapshot = vforge.make_snapshot("backend", "before-test")

        self.assertEqual(snapshot, "before-test")
        commands = [invocation.args[0] for invocation in call.call_args_list]
        self.assertIn(["container", "stop", "--time", "10", "vforge-backend"], commands)
        self.assertIn(["container", "commit", "--pause=true", "vforge-backend", "localhost/vforge-snapshot:backend-before-test"], commands)
        self.assertEqual(commands[-1], ["container", "start", "vforge-backend"])
        entry = save.call_args.args[1][0]
        self.assertEqual(entry["environment"], record)
        self.assertEqual(entry["root_id"], "b" * 32)
        self.assertEqual(entry["base_digest"], "a" * 64)
        self.assertEqual(entry["image_version"], "0.1.0")

    def test_integrated_snapshot_journals_and_rotates_frozen_uppers_instead_of_copying(self) -> None:
        container = {"State": {"Status": "running"}}
        metadata = {"type": "named", "isolation": "integrated", "layer_id": "a" * 32}
        with patch.object(vforge, "require_environment", return_value=("vforge-backend", container)), \
             patch.object(vforge, "env_state", return_value={"backend": metadata}), \
             patch.object(vforge, "podman") as podman:
            with self.assertRaisesRegex(vforge.VforgeError, "legacy Fedora/host-overlay"):
                vforge.make_snapshot("backend", "frozen-state")
        podman.assert_not_called()
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
        with self.assertRaisesRegex(vforge.VforgeError, "legacy Fedora/host-overlay"):
            vforge.recover_snapshot_transaction("backend", {"root_mode": "fedora-overlay"})
    def test_snapshot_journal_is_retained_when_snapshot_image_is_missing(self) -> None:
        with patch.object(vforge, "podman") as podman:
            with self.assertRaisesRegex(vforge.VforgeError, "legacy Fedora/host-overlay"):
                vforge.recover_snapshot_transaction("backend", {"root_mode": "fedora-overlay"})
        podman.assert_not_called()
    def test_rollback_journal_recovery_recreates_checkpoint_after_interrupted_switch(self) -> None:
        with patch.object(vforge, "podman") as podman, patch.object(vforge, "create_container") as create:
            with self.assertRaisesRegex(vforge.VforgeError, "legacy Fedora/host-overlay"):
                vforge.recover_rollback_transaction("backend", {"root_mode": "fedora-overlay"})
        podman.assert_not_called()
        create.assert_not_called()
    def test_rollback_selects_frozen_snapshot_stack_without_copying_upper_trees(self) -> None:
        with patch.object(vforge, "require_environment", return_value=("vforge-backend", {})) as environment, patch.object(vforge, "podman") as podman:
            with patch.object(vforge, "env_state", return_value={"backend": {"root_mode": "fedora-overlay"}}):
                with self.assertRaisesRegex(vforge.VforgeError, "legacy Fedora/host-overlay"):
                    vforge.rollback_to("backend", "known-good", confirm=False)
        environment.assert_called_once()
        podman.assert_not_called()
    def test_legacy_snapshot_rollback_uses_a_fresh_layer_to_preserve_checkpoint_upper(self) -> None:
        with patch.object(vforge, "require_environment", return_value=("vforge-backend", {})) as environment, patch.object(vforge, "podman") as podman:
            with patch.object(vforge, "env_state", return_value={"backend": {"root_mode": "fedora-overlay"}}):
                with self.assertRaisesRegex(vforge.VforgeError, "legacy Fedora/host-overlay"):
                    vforge.rollback_to("backend", "legacy", confirm=False)
        environment.assert_called_once()
        podman.assert_not_called()

if __name__ == "__main__":
    unittest.main()
