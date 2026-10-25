from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
import subprocess
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

    def test_dnf_is_limited_to_the_selected_veyra_channel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            channel_file = Path(temporary) / "channel"
            channel_file.write_text("testing\n", encoding="utf-8")
            with patch.object(vpkg, "CHANNEL_FILE", channel_file):
                self.assertIn("--repo=veyra-testing", vpkg.dnf_prefix())
                self.assertIn("--repo=veyra-main,veyra-testing", vpkg.dnf_prefix("both"))

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

                inspect.assert_called_once_with("upgrade")
                dnf.assert_called_once_with(
                    ["upgrade", "--installed-from-repo=veyra-main,veyra-testing"],
                    channel=channel,
                )

    def test_upgrade_preflight_refreshes_repository_metadata(self) -> None:
        with (
            patch.object(vpkg, "selected_channel", return_value="testing"),
            patch.object(vpkg, "run") as run,
            patch.object(vpkg, "inspect_extension_rpms", return_value=[]),
        ):
            self.assertFalse(vpkg.download_for_inspection("upgrade"))

        command = run.call_args.args[0]
        self.assertIn("--refresh", command)
        self.assertLess(command.index("--refresh"), command.index("upgrade"))

    def test_extension_rpm_policy_allows_only_var_extension_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            rpm_file = Path(temporary) / "veyra-shell-tools.rpm"
            rpm_file.touch()
            responses = [
                subprocess.CompletedProcess([], 0, "veyra-shell-tools", ""),
                subprocess.CompletedProcess(
                    [], 0, "/var\n/var/lib\n/var/lib/extensions\n/var/lib/extensions/veyra-shell-tools.raw\n", ""
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
            package_image.touch()
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


if __name__ == "__main__":
    unittest.main()
