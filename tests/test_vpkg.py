from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import json
import subprocess
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "mkosi.extra/usr/bin/vpkg"
SPEC = importlib.util.spec_from_loader("veyra_vpkg", SourceFileLoader("veyra_vpkg", str(SCRIPT)))
assert SPEC and SPEC.loader
vpkg = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vpkg)


class VpkgPolicyTests(unittest.TestCase):
    def test_upstream_package_policy_blocks_kernel_init_boot_and_driver_packages(self) -> None:
        for name in (
            "kernel-core",
            "akmod-nvidia",
            "nvidia-driver",
            "grub2-efi-x64",
            "shim-x64",
            "systemd",
            "mesa-dri-drivers",
        ):
            with self.subTest(name=name), self.assertRaises(vpkg.VpkgError):
                vpkg.validate_user_package_name(name)

    def test_upstream_package_policy_accepts_application_package_names(self) -> None:
        for name in ("nodejs", "python3-pip", "vlc", "discord"):
            with self.subTest(name=name):
                vpkg.validate_user_package_name(name)

    def test_upstream_payload_must_be_additive_usr_or_opt_content(self) -> None:
        vpkg.validate_user_package_paths([
            "/usr/bin/example",
            "/usr/share/applications/example.desktop",
            "/opt/example/bin/tool",
        ])
        for path in (
            "/usr/lib/modules/6.15.0/vmlinuz",
            "/usr/lib/systemd/systemd",
            "/usr/lib/systemd/boot/efi/systemd-bootx64.efi",
            "/boot/efi/EFI/BOOT/BOOTX64.EFI",
            "/etc/ld.so.conf.d/example.conf",
            "/var/lib/example/state",
        ):
            with self.subTest(path=path), self.assertRaises(vpkg.VpkgError):
                vpkg.validate_user_package_paths([path])

    def test_upstream_payload_rejects_path_traversal(self) -> None:
        for path in ("usr/bin/example", "/usr/bin/../sbin/init", "/usr//bin/example"):
            with self.subTest(path=path), self.assertRaises(vpkg.VpkgError):
                vpkg.validate_user_package_paths([path])

    def test_upstream_dnf_root_is_separate_and_uses_only_fedora_sources(self) -> None:
        installroot = Path("/var/lib/veyra/vpkg/staging-root")
        prefix = vpkg.user_package_dnf_prefix(installroot)
        self.assertIn("--installroot=" + str(installroot), prefix)
        self.assertIn("--use-host-config", prefix)
        self.assertIn(
            "--repo=" + ",".join((*vpkg.FEDORA_REPOS, *vpkg.RPMFUSION_REPOS)),
            prefix,
        )
        self.assertTrue(any("localpkg_gpgcheck=True" in item for item in prefix))
        self.assertFalse(any("veyra-main" in item or "veyra-testing" in item for item in prefix))

    def test_user_app_rpm_transaction_keeps_base_inventory_unchanged(self) -> None:
        base = {"bash": {"bash-0:5.2-1.x86_64"}}
        installed = {
            **base,
            "tree": {"tree-0:2.1.1-1.x86_64"},
            "gpg-pubkey": {"gpg-pubkey-123"},
        }
        with patch.object(vpkg, "rpm_records", return_value=installed):
            added = vpkg.package_records_after_install(Path("/stage"), base)
        self.assertEqual(added, {"tree": "tree-0:2.1.1-1.x86_64"})

    def test_user_app_rpm_transaction_rejects_base_package_replacement(self) -> None:
        base = {"systemd": {"systemd-0:257-1.x86_64"}}
        installed = {"systemd": {"systemd-0:258-1.x86_64"}}
        with patch.object(vpkg, "rpm_records", return_value=installed):
            with self.assertRaisesRegex(vpkg.VpkgError, "changes base package systemd"):
                vpkg.package_records_after_install(Path("/stage"), base)

    def test_user_app_rpm_payload_policy_accepts_additive_application_files(self) -> None:
        regular = stat.S_IFREG | 0o755
        symlink = stat.S_IFLNK | 0o777
        output = (
            f"/opt/veyra-vpkg-test-31/bin/example\t{regular}\t(none)\t(none)\n"
            f"/opt/veyra-vpkg-test-31/bin/example-link\t{symlink}\t../lib/example\t(none)\n"
        )
        with patch.object(
            vpkg,
            "run",
            return_value=subprocess.CompletedProcess([], 0, output, ""),
        ):
            vpkg.validate_installed_app_payloads(Path("/stage"), {"example": "example-1"})

    def test_app_tree_allows_shared_base_directories_but_rejects_file_collisions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            tree = Path(temporary)
            (tree / "usr/bin").mkdir(parents=True)
            (tree / "usr/bin/example").write_text("app", encoding="utf-8")
            with patch("pathlib.Path.exists", autospec=True, side_effect=lambda path: str(path) == "/usr/bin"):
                vpkg.validate_user_app_tree(tree)

            (tree / "usr/bin/bash").write_text("collision", encoding="utf-8")
            with patch("pathlib.Path.exists", autospec=True, side_effect=lambda path: str(path) in {"/usr/bin", "/usr/bin/bash"}):
                with self.assertRaisesRegex(vpkg.VpkgError, "would replace an existing base-system path"):
                    vpkg.validate_user_app_tree(tree)

    def test_user_app_rpm_payload_policy_rejects_etc_configuration(self) -> None:
        mode = stat.S_IFREG | 0o644
        output = f"/etc/ld.so.conf.d/example.conf\t{mode}\t(none)\t(none)\n"
        with patch.object(
            vpkg,
            "run",
            return_value=subprocess.CompletedProcess([], 0, output, ""),
        ):
            with self.assertRaisesRegex(vpkg.VpkgError, "outside /usr or /opt"):
                vpkg.validate_installed_app_payloads(Path("/stage"), {"example": "example-1"})

    def test_user_app_rpm_payload_policy_rejects_special_files_and_capabilities(self) -> None:
        for mode, capabilities, message in (
            (stat.S_IFIFO | 0o600, "(none)", "special app RPM file"),
            (stat.S_IFREG | 0o755, "cap_net_raw+ep", "Linux capabilities"),
        ):
            output = f"/usr/bin/example\t{mode}\t(none)\t{capabilities}\n"
            with (
                self.subTest(message=message),
                patch.object(
                    vpkg,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, output, ""),
                ),
            ):
                with self.assertRaisesRegex(vpkg.VpkgError, message):
                    vpkg.validate_installed_app_payloads(Path("/stage"), {"example": "example-1"})

    def test_app_generation_copies_only_the_candidate_tree_and_writes_manifest(self) -> None:
        base = {"bash": {"bash-0:5.2-1.x86_64"}}
        added = {**base, "tree": {"tree-0:2.1.1-1.x86_64"}}
        with tempfile.TemporaryDirectory() as temporary:
            layer_dir = Path(temporary) / "layers"

            def seed(root: Path) -> dict[str, set[str]]:
                (root / "usr/lib/sysimage/rpm").mkdir(parents=True)
                (root / "usr/lib/sysimage/rpm/Packages.db").touch()
                app_file = root / "opt/vforge-fixture/bin/example"
                app_file.parent.mkdir(parents=True)
                app_file.write_text("candidate only", encoding="utf-8")
                return base

            with (
                patch.object(vpkg, "USER_APP_LAYER_DIR", layer_dir),
                patch.object(vpkg, "active_user_app_generation", return_value=None),
                patch.object(vpkg, "seed_user_app_installroot", side_effect=seed),
                patch.object(vpkg, "rpm_records", return_value=added),
                patch.object(vpkg, "rpm_database_path", return_value=Path("/usr/lib/sysimage/rpm")),
                patch.object(vpkg, "user_package_dnf_prefix", return_value=["dnf5", "--installroot=/tmp/root"]),
                patch.object(vpkg, "extension_release_text", return_value="ID=fedora\nVERSION_ID=44\nSYSEXT_LEVEL=test\n"),
                patch.object(vpkg, "validate_installed_app_payloads"),
                patch.object(vpkg, "run") as run,
            ):
                generation = vpkg.build_user_app_generation(
                    [{"kind": "repo", "name": "tree"}], {}
                )

            self.assertTrue((generation / "tree/opt/vforge-fixture/bin/example").is_file())
            self.assertFalse((generation / "tree/usr/lib/sysimage/rpm").exists())
            self.assertEqual(
                json.loads((generation / "manifest.json").read_text(encoding="utf-8"))["installed"],
                {"tree": "tree-0:2.1.1-1.x86_64"},
            )
            self.assertEqual(run.call_count, 1)

    def test_sync_app_extension_link_follows_the_persistent_generation_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            layer_dir = root / "layers"
            generation = layer_dir / "generation-test"
            tree = generation / "tree"
            tree.mkdir(parents=True)
            (generation / "manifest.json").write_text(
                json.dumps({"schema": 1, "requests": [], "installed": {}}),
                encoding="utf-8",
            )
            state_dir = root / "apps"
            state_dir.mkdir()
            current = state_dir / "current"
            current.symlink_to(generation, target_is_directory=True)
            extension_link = root / "etc/extensions/vpkg-apps"
            with (
                patch.object(vpkg, "USER_APP_LAYER_DIR", layer_dir),
                patch.object(vpkg, "USER_APP_STATE", state_dir),
                patch.object(vpkg, "USER_APP_CURRENT", current),
                patch.object(vpkg, "USER_APP_SYSEXT_LINK", extension_link),
            ):
                vpkg.sync_user_app_extension_link()
                self.assertEqual(extension_link.resolve(), tree.resolve())
                vpkg.set_user_app_current(None)
                vpkg.sync_user_app_extension_link()
            self.assertFalse(extension_link.exists())
            self.assertFalse(extension_link.is_symlink())

    def test_failed_app_layer_activation_restores_previous_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layer_dir = Path(temporary) / "layers"
            previous = layer_dir / "generation-old"
            candidate = layer_dir / "generation-new"
            previous.mkdir(parents=True)
            candidate.mkdir()
            state_dir = Path(temporary) / "apps"
            state_dir.mkdir()
            current = state_dir / "current"
            current.symlink_to(previous, target_is_directory=True)
            with (
                patch.object(vpkg, "USER_APP_LAYER_DIR", layer_dir),
                patch.object(vpkg, "USER_APP_STATE", state_dir),
                patch.object(vpkg, "USER_APP_CURRENT", current),
                patch.object(vpkg, "active_user_app_generation", return_value=previous),
                patch.object(vpkg, "sync_user_app_extension_link"),
                patch.object(
                    vpkg,
                    "run",
                    side_effect=[vpkg.VpkgError("simulated refresh failure"), subprocess.CompletedProcess([], 0)],
                ),
                patch.object(vpkg, "check_extensions_active"),
            ):
                with self.assertRaisesRegex(vpkg.VpkgError, "previous app set was restored"):
                    vpkg.activate_user_app_generation(candidate)
            self.assertEqual(current.resolve(), previous.resolve())
            self.assertFalse(candidate.exists())

    def test_boot_health_rejects_app_files_that_collide_with_a_new_base_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            generation = Path(temporary) / "generation"
            tree = generation / "tree"
            app_file = tree / "usr/bin/example-app"
            app_file.parent.mkdir(parents=True)
            app_file.write_text("application", encoding="utf-8")
            (generation / "manifest.json").write_text(
                json.dumps({"schema": 1, "requests": [], "installed": {}}),
                encoding="utf-8",
            )
            with (
                patch.object(vpkg, "active_user_app_generation", return_value=generation),
                patch.object(
                    vpkg,
                    "run",
                    return_value=subprocess.CompletedProcess(
                        [],
                        0,
                        "/usr/bin/example-app\t33261\n/usr/bin/other-base-file\t33261\n",
                        "",
                    ),
                ),
            ):
                with self.assertRaisesRegex(vpkg.VpkgError, "conflicts with files in this base image"):
                    vpkg.check_user_app_base_conflicts()

    def test_boot_health_ignores_shared_base_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            generation = Path(temporary) / "generation"
            tree = generation / "tree"
            app_file = tree / "usr/bin/example-app"
            app_file.parent.mkdir(parents=True)
            app_file.write_text("application", encoding="utf-8")
            (generation / "manifest.json").write_text(
                json.dumps({"schema": 1, "requests": [], "installed": {}}),
                encoding="utf-8",
            )
            output = "/usr/bin\t16384\n/usr/bin/bash\t33261\n"
            with (
                patch.object(vpkg, "active_user_app_generation", return_value=generation),
                patch.object(
                    vpkg,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, output, ""),
                ),
            ):
                vpkg.check_user_app_base_conflicts()

    def test_accepts_only_exact_veyra_package_names(self) -> None:
        vpkg.validate_package_specs(["veyra-shell-tools", "veyra-codecs-1"])

    def test_rejects_fedora_names_globs_and_paths(self) -> None:
        for name in ("bash", "veyra-shell-*", "./veyra-shell-tools.rpm", "veyra-shell-tools;id"):
            with self.subTest(name=name), self.assertRaises(vpkg.VpkgError):
                vpkg.validate_package_specs([name])

    def test_default_and_saved_channels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            channel_file = Path(temporary) / "channel"
            with patch.object(vpkg, "CHANNEL_FILE", channel_file):
                self.assertEqual(vpkg.selected_channel(), "main")
                channel_file.write_text("testing\n", encoding="utf-8")
                self.assertEqual(vpkg.selected_channel(), "testing")
                channel_file.write_text("unknown\n", encoding="utf-8")
                with self.assertRaises(vpkg.VpkgError):
                    vpkg.selected_channel()

    def test_dnf_uses_fedora_rpmfusion_and_selected_veyra_channels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            channel_file = Path(temporary) / "channel"
            channel_file.write_text("testing\n", encoding="utf-8")
            key_file = Path(temporary) / "RPM-GPG-KEY-VEYRA"
            key_file.touch()
            with (
                patch.object(vpkg, "CHANNEL_FILE", channel_file),
                patch.object(vpkg, "VPKG_PUBLIC_KEY", key_file),
            ):
                selected = next(
                    item for item in vpkg.dnf_prefix() if item.startswith("--repo=")
                )
                self.assertEqual(
                    selected.removeprefix("--repo=").split(","),
                    [
                        "fedora",
                        "updates",
                        "rpmfusion-free",
                        "rpmfusion-free-updates",
                        "rpmfusion-nonfree",
                        "rpmfusion-nonfree-updates",
                        "veyra-testing",
                    ],
                )
                both = next(
                    item for item in vpkg.dnf_prefix("both") if item.startswith("--repo=")
                )
                self.assertTrue(both.endswith("veyra-main,veyra-testing"))

    def test_fedora_queries_work_without_a_veyra_signing_key(self) -> None:
        with patch.object(vpkg, "VPKG_PUBLIC_KEY", Path("/missing/veyra-key")):
            selected = next(
                item for item in vpkg.dnf_prefix() if item.startswith("--repo=")
            )
        self.assertEqual(
            selected.removeprefix("--repo=").split(","),
            [*vpkg.FEDORA_REPOS, *vpkg.RPMFUSION_REPOS],
        )

    def test_channel_switch_uses_the_package_transaction_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            channel_file = Path(temporary) / "veyra/vpkg/channel"
            with (
                patch.object(vpkg, "require_root"),
                patch.object(vpkg, "CHANNEL_FILE", channel_file),
                patch.object(vpkg, "with_lock", side_effect=lambda action: action()) as lock,
            ):
                vpkg.set_channel("testing")

            lock.assert_called_once()
            self.assertEqual(channel_file.read_text(encoding="utf-8"), "testing\n")

    def test_install_uses_one_channel_snapshot_for_preflight_and_commit(self) -> None:
        for channel in ("main", "testing"):
            with self.subTest(channel=channel):
                with (
                    patch.object(vpkg, "require_root"),
                    patch.object(vpkg, "selected_channel", return_value=channel),
                    patch.object(vpkg, "download_for_inspection", return_value=True) as inspect,
                    patch.object(vpkg, "latest_history_id", return_value=7),
                    patch.object(vpkg, "dnf") as dnf,
                    patch.object(vpkg, "refresh_extensions_or_undo"),
                    patch.object(vpkg, "with_lock", side_effect=lambda action: action()),
                ):
                    vpkg.install(["veyra-shell-tools"])

                inspect.assert_called_once_with("install", ["veyra-shell-tools"], channel=channel)
                dnf.assert_called_once_with(
                    ["install", f"--from-repo={vpkg.CHANNELS[channel]}", "veyra-shell-tools"],
                    channel=channel,
                )

    def test_upgrade_transaction_uses_same_channel_as_preflight_download(self) -> None:
        for channel in ("main", "testing"):
            with self.subTest(channel=channel):
                with (
                    patch.object(vpkg, "require_root"),
                    patch.object(vpkg, "selected_channel", return_value=channel),
                    patch.object(vpkg, "download_for_inspection", return_value=True) as inspect,
                    patch.object(vpkg, "latest_history_id", return_value=7),
                    patch.object(vpkg, "dnf") as dnf,
                    patch.object(vpkg, "refresh_extensions_or_undo"),
                    patch.object(vpkg, "with_lock", side_effect=lambda action: action()),
                ):
                    vpkg.upgrade()

                inspect.assert_called_once_with("upgrade", channel=channel)
                dnf.assert_called_once_with(
                    ["upgrade", "--installed-from-repo=veyra-main,veyra-testing"],
                    channel=channel,
                )

    def test_upgrade_preflight_invalidates_and_refreshes_repository_metadata(self) -> None:
        with (
            patch.object(vpkg, "selected_channel", return_value="testing"),
            patch.object(vpkg, "run") as run,
            patch.object(vpkg, "inspect_extension_rpms", return_value=[]),
        ):
            self.assertFalse(vpkg.download_for_inspection("upgrade"))

        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0][-2:], ["clean", "metadata"])
        self.assertIn("--refresh", commands[1])
        self.assertLess(commands[1].index("--refresh"), commands[1].index("upgrade"))

    def test_extension_rpm_policy_allows_only_var_extension_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rpm_file = Path(temporary) / "veyra-shell-tools.rpm"
            rpm_file.touch()
            responses = [
                subprocess.CompletedProcess([], 0, "veyra-shell-tools", ""),
                subprocess.CompletedProcess(
                    [], 0, "/var\n/var/lib\n/var/lib/extensions\n/var/lib/extensions/veyra-shell-tools.raw\n", ""
                ),
                subprocess.CompletedProcess(
                    [], 0,
                    "drwxr-xr-xr-x\t/var\n"
                    "drwxr-xr-xr-x\t/var/lib\n"
                    "drwxr-xr-xr-x\t/var/lib/extensions\n"
                    "-rw-r--r--\t/var/lib/extensions/veyra-shell-tools.raw\n",
                    "",
                ),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "absent", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "digests signatures OK", ""),
            ]
            with patch.object(vpkg, "run", side_effect=responses):
                self.assertEqual(vpkg.inspect_extension_rpms(Path(temporary)), [rpm_file])

    def test_extension_rpm_policy_rejects_symlink_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rpm_file = Path(temporary) / "veyra-bad.rpm"
            rpm_file.touch()
            responses = [
                subprocess.CompletedProcess([], 0, "veyra-bad", ""),
                subprocess.CompletedProcess(
                    [], 0, "/var/lib/extensions/veyra-bad.raw\n", ""
                ),
                subprocess.CompletedProcess(
                    [], 0, "lrwxrwxrwx\t/var/lib/extensions/veyra-bad.raw\n", ""
                ),
            ]
            with patch.object(vpkg, "run", side_effect=responses):
                with self.assertRaisesRegex(vpkg.VpkgError, "regular, non-executable"):
                    vpkg.inspect_extension_rpms(Path(temporary))

    def test_extension_rpm_policy_rejects_system_file_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rpm_file = Path(temporary) / "veyra-bad.rpm"
            rpm_file.touch()
            responses = [
                subprocess.CompletedProcess([], 0, "veyra-bad", ""),
                subprocess.CompletedProcess([], 0, "/usr/bin/unsafe\n", ""),
            ]
            with patch.object(vpkg, "run", side_effect=responses):
                with self.assertRaisesRegex(vpkg.VpkgError, "System files must be delivered"):
                    vpkg.inspect_extension_rpms(Path(temporary))

    def test_extension_rpm_policy_rejects_rpm_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rpm_file = Path(temporary) / "veyra-bad.rpm"
            rpm_file.touch()
            responses = [
                subprocess.CompletedProcess([], 0, "veyra-bad", ""),
                subprocess.CompletedProcess([], 0, "/var/lib/extensions/veyra-bad.raw\n", ""),
                subprocess.CompletedProcess(
                    [], 0, "-rw-r--r--\t/var/lib/extensions/veyra-bad.raw\n", ""
                ),
                subprocess.CompletedProcess([], 0, "postinstall scriptlet", ""),
            ]
            with patch.object(vpkg, "run", side_effect=responses):
                with self.assertRaisesRegex(vpkg.VpkgError, "executable RPM scripts"):
                    vpkg.inspect_extension_rpms(Path(temporary))

    def test_extension_rpm_policy_rejects_transaction_file_triggers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rpm_file = Path(temporary) / "veyra-bad.rpm"
            rpm_file.touch()
            responses = [
                subprocess.CompletedProcess([], 0, "veyra-bad", ""),
                subprocess.CompletedProcess([], 0, "/var/lib/extensions/veyra-bad.raw\n", ""),
                subprocess.CompletedProcess(
                    [], 0, "-rw-r--r--\t/var/lib/extensions/veyra-bad.raw\n", ""
                ),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 0, "present", ""),
            ]
            with patch.object(vpkg, "run", side_effect=responses):
                with self.assertRaisesRegex(vpkg.VpkgError, "transaction file triggers"):
                    vpkg.inspect_extension_rpms(Path(temporary))

    def test_sync_links_extension_from_var_installroot_and_ignores_imported_key(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            host_extensions = Path(temporary) / "etc-extensions"
            package_image = root / "var/lib/extensions/veyra-shell-tools.raw"
            package_image.parent.mkdir(parents=True)
            package_image.write_bytes(b"extension image")
            with (
                patch.object(vpkg, "VPKG_ROOT", root),
                patch.object(vpkg, "SYSTEM_EXTENSION_LINK_DIR", host_extensions),
                patch.object(
                    vpkg,
                    "run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, "gpg-pubkey-123\nveyra-shell-tools\n", ""),
                        subprocess.CompletedProcess(
                            [], 0,
                            "/var\n/var/lib\n/var/lib/extensions\n/var/lib/extensions/veyra-shell-tools.raw\n",
                            "",
                        ),
                    ],
                ),
            ):
                vpkg.sync_extension_links()
            link = host_extensions / "veyra-shell-tools.raw"
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.resolve(), package_image.resolve())

    def test_sync_rejects_symlinked_extension_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            host_extensions = Path(temporary) / "etc-extensions"
            package_image = root / "var/lib/extensions/veyra-shell-tools.raw"
            package_image.parent.mkdir(parents=True)
            package_image.symlink_to("/etc/passwd")
            with (
                patch.object(vpkg, "VPKG_ROOT", root),
                patch.object(vpkg, "SYSTEM_EXTENSION_LINK_DIR", host_extensions),
                patch.object(
                    vpkg,
                    "run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, "veyra-shell-tools\n", ""),
                        subprocess.CompletedProcess(
                            [], 0,
                            "/var\n/var/lib\n/var/lib/extensions\n"
                            "/var/lib/extensions/veyra-shell-tools.raw\n",
                            "",
                        ),
                    ],
                ),
            ):
                with self.assertRaisesRegex(vpkg.VpkgError, "non-empty regular file"):
                    vpkg.sync_extension_links()

    def test_sync_removes_stale_vpkg_link_after_package_removal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            host_extensions = Path(temporary) / "etc-extensions"
            host_extensions.mkdir()
            target = root / "var/lib/extensions/veyra-shell-tools.raw"
            target.parent.mkdir(parents=True)
            target.touch()
            link = host_extensions / "veyra-shell-tools.raw"
            link.symlink_to(target)
            with (
                patch.object(vpkg, "VPKG_ROOT", root),
                patch.object(vpkg, "SYSTEM_EXTENSION_LINK_DIR", host_extensions),
                patch.object(
                    vpkg,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0, "", ""),
                ),
            ):
                vpkg.sync_extension_links()
            self.assertFalse(link.exists())
            self.assertFalse(link.is_symlink())

    def test_extension_health_check_requires_every_installed_extension_active(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            with (
                patch.object(vpkg, "VPKG_ROOT", root),
                patch.object(
                    vpkg,
                    "run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, "veyra-shell-tools\n", ""),
                        subprocess.CompletedProcess(
                            [], 0,
                            '[{"hierarchy":"/usr","extensions":["veyra-shell-tools"],"since":null}]',
                            "",
                        ),
                    ],
                ) as run,
            ):
                vpkg.check_extensions_active()

            self.assertEqual(run.call_count, 2)

    def test_extension_health_check_rejects_inactive_installed_extension(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            with (
                patch.object(vpkg, "VPKG_ROOT", root),
                patch.object(
                    vpkg,
                    "run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, "veyra-shell-tools\n", ""),
                        subprocess.CompletedProcess(
                            [], 0,
                            '[{"hierarchy":"/usr","extensions":[],"since":null}]',
                            "",
                        ),
                    ],
                ),
            ):
                with self.assertRaisesRegex(vpkg.VpkgError, "did not activate"):
                    vpkg.check_extensions_active()

    def test_extension_health_check_preserves_raw_suffix_in_package_name(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            root.mkdir()
            with (
                patch.object(vpkg, "VPKG_ROOT", root),
                patch.object(
                    vpkg,
                    "run",
                    side_effect=[
                        subprocess.CompletedProcess([], 0, "veyra-example.raw\n", ""),
                        subprocess.CompletedProcess(
                            [], 0,
                            '[{"hierarchy":"/usr","extensions":["veyra-example.raw"],"since":null}]',
                            "",
                        ),
                    ],
                ),
            ):
                vpkg.check_extensions_active()


if __name__ == "__main__":
    unittest.main()
