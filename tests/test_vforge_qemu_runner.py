from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/run-vforge-qemu.py"
SPEC = importlib.util.spec_from_file_location("vforge_qemu_runner", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)
FIXTURE_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/test-vforge-integration-qemu.py"
FIXTURE_SPEC = importlib.util.spec_from_file_location("vforge_guest_fixture", FIXTURE_SCRIPT)
assert FIXTURE_SPEC is not None and FIXTURE_SPEC.loader is not None
fixture = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(fixture)


class VforgeQemuRunnerTests(unittest.TestCase):
    def test_partition_offsets_use_gpt_sector_size(self) -> None:
        layout = {
            "partitiontable": {
                "sectorsize": 512,
                "partitions": [
                    {"start": 2048, "size": 2097152, "name": "esp"},
                    {"start": 37750784, "size": 1048576, "name": "VEYRA_ETC"},
                    {"start": 38799360, "size": 25165824, "name": "VEYRA_VAR"},
                    {"start": 63965184, "size": 50331648, "name": "home"},
                ],
            }
        }

        offsets = runner.partition_offsets(json.dumps(layout))

        self.assertEqual(offsets["VEYRA_ETC"], (19_328_401_408, 536_870_912))
        self.assertEqual(offsets["home"], (32_750_174_208, 25_769_803_776))

    def test_base_gate_accepts_fedora44_and_rejects_other_fedora_targets(self) -> None:
        release = 'ID=fedora\nVERSION_ID="44"\nIMAGE_ID="veyra-next"\n'
        self.assertEqual(runner.require_fedora44_veyra(release)["VERSION_ID"], "44")

        with self.assertRaisesRegex(runner.VforgeQemuError, "current Fedora 44"):
            runner.require_fedora44_veyra("ID=fedora\nVERSION_ID=46\nIMAGE_ID=veyra-next\n")

    def test_qemu_base_gate_rejects_fedora44_image_without_size_cap_tools(self) -> None:
        manifest = json.dumps({"packages": [
            {"type": "rpm", "name": "e2fsprogs-libs"},
            {"type": "rpm", "name": "podman"},
        ]})

        with self.assertRaisesRegex(runner.VforgeQemuError, "e2fsprogs"):
            runner.require_size_cap_packages(manifest)

    def test_qemu_base_gate_accepts_fedora44_image_with_size_cap_tools(self) -> None:
        manifest = json.dumps({"packages": [
            {"type": "rpm", "name": "e2fsprogs"},
            {"type": "rpm", "name": "podman"},
        ]})

        runner.require_size_cap_packages(manifest)

    def test_partition_layout_requires_persistent_etc_and_home(self) -> None:
        layout = {"partitiontable": {"sectorsize": 512, "partitions": [
            {"start": 1, "size": 2, "name": "VEYRA_ETC"},
            {"start": 3, "size": 4, "name": "esp"},
            {"start": 5, "size": 6, "name": "VEYRA_VAR"},
        ]}}

        with self.assertRaisesRegex(runner.VforgeQemuError, "home"):
            runner.partition_offsets(json.dumps(layout))

    def test_injects_locked_normal_user_and_rootless_subid_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            etc = pathlib.Path(temporary) / "etc"
            home = pathlib.Path(temporary) / "home"
            etc.mkdir()
            home.mkdir()
            for name, contents in {
                "passwd": "root:x:0:0:root:/root:/bin/bash\n",
                "group": "root:x:0:\n",
                "shadow": "root:!:20000:0:99999:7:::\n",
            }.items():
                (etc / name).write_text(contents, encoding="utf-8")

            with patch.object(runner.os, "chown") as chown, patch.object(runner.os, "fchown") as fchown:
                runner.inject_test_user(etc, home)

            self.assertIn(
                "vforge-test:x:4242:4242:Vforge QEMU test:/home/vforge-test:/bin/bash",
                (etc / "passwd").read_text(encoding="utf-8"),
            )
            self.assertIn("vforge-test:x:4242:", (etc / "group").read_text(encoding="utf-8"))
            self.assertIn("vforge-test:!:", (etc / "shadow").read_text(encoding="utf-8"))
            self.assertEqual((etc / "subuid").read_text(encoding="utf-8"), "vforge-test:200000:65536\n")
            self.assertEqual((etc / "subgid").read_text(encoding="utf-8"), "vforge-test:200000:65536\n")
            self.assertTrue((home / "vforge-test").is_dir())
            chown.assert_called_once_with(home / "vforge-test", 4242, 4242)
            self.assertEqual(fchown.call_count, 5)

    def test_refuses_existing_account_or_uid_collision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            etc = pathlib.Path(temporary) / "etc"
            home = pathlib.Path(temporary) / "home"
            etc.mkdir()
            home.mkdir()
            (etc / "passwd").write_text("other:x:4242:4242:existing:/home/other:/bin/bash\n", encoding="utf-8")
            (etc / "group").write_text("root:x:0:\n", encoding="utf-8")
            (etc / "shadow").write_text("root:!:20000:0:99999:7:::\n", encoding="utf-8")

            with self.assertRaisesRegex(runner.VforgeQemuError, "UID 4242"):
                runner.inject_test_user(etc, home)

    def test_initializes_machine_id_to_avoid_systemd_firstboot_preset_removing_fixture_service(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            etc = pathlib.Path(temporary) / "etc"
            etc.mkdir()
            machine_id_path = etc / "machine-id"
            machine_id_path.write_text("uninitialized\n", encoding="ascii")

            with patch.object(runner.os, "fchown") as fchown:
                machine_id = runner.inject_test_machine_id(etc)

            self.assertRegex(machine_id, re.compile(r"^[0-9a-f]{32}$"))
            self.assertEqual(machine_id_path.read_text(encoding="ascii"), machine_id + "\n")
            self.assertEqual(machine_id_path.stat().st_mode & 0o777, 0o444)
            fchown.assert_called_once()

    def test_rejects_symlinked_guest_machine_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            etc = pathlib.Path(temporary) / "etc"
            etc.mkdir()
            target = pathlib.Path(temporary) / "machine-id-target"
            target.write_text("uninitialized\n", encoding="ascii")
            (etc / "machine-id").symlink_to(target)

            with self.assertRaisesRegex(runner.VforgeQemuError, "regular file"):
                runner.inject_test_machine_id(etc)

    def test_guest_boot_runner_is_valid_shell_and_uses_ro_source_as_normal_user(self) -> None:
        script = runner.test_runner_script("veyra-base.tar")

        result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("version=9p2000.L,ro vforge-source", script)
        self.assertIn("version=9p2000.L,ro vforge-base", script)
        self.assertIn("VFORGE_BASE_ARCHIVE=\"/run/vforge-base/veyra-base.tar\"", script)
        self.assertIn("runuser --user vforge-test", script)
        self.assertIn(runner.PASS_MARKER, script)
        self.assertIn(runner.FAIL_MARKER, script)

    def test_qemu_command_uses_kvm_and_mounts_checkout_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            code = root / "OVMF_CODE.fd"
            variables = root / "OVMF_VARS.fd"
            variables_copy = root / "OVMF_VARS.copy.fd"
            archive = root / "veyra-base.tar"
            for path in (code, variables):
                path.write_bytes(b"firmware")
            archive.write_bytes(b"oci archive")
            command = runner.qemu_command(
                "qemu-system-x86_64", root / "guest.raw", root / "checkout",
                code, variables, variables_copy, root / "serial.log", archive,
            )

        self.assertIn("q35,accel=kvm", command)
        self.assertTrue(any("readonly=on" in item for item in command))
        self.assertTrue(any("security_model=none,readonly=on" in item for item in command))
        self.assertTrue(any("mount_tag=vforge-base" in item and str(root) in item for item in command))

    def test_qemu_command_supports_tcg_without_host_cpu_passthrough(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            code = root / "OVMF_CODE.fd"
            variables = root / "OVMF_VARS.fd"
            archive = root / "veyra-base.tar"
            for path in (code, variables):
                path.write_bytes(b"firmware")
            archive.write_bytes(b"oci archive")
            command = runner.qemu_command(
                "qemu-system-x86_64", root / "guest.raw", root / "checkout",
                code, variables, root / "OVMF_VARS.copy.fd", root / "serial.log", archive,
                acceleration="tcg",
            )

        self.assertIn("-accel", command)
        self.assertIn("tcg,thread=multi", command)
        self.assertIn("max", command)
        self.assertNotIn("host", command)

    def test_selects_tcg_when_kvm_is_unavailable(self) -> None:
        with patch.object(runner.os, "geteuid", return_value=4242), patch.object(
            runner.os, "access", return_value=False
        ), patch.object(
            runner.shutil, "which", return_value="/usr/bin/qemu-system-x86_64"
        ), patch.object(runner, "run", return_value=subprocess.CompletedProcess([], 0, stdout="tcg\nkvm\n", stderr="")):
            qemu, acceleration = runner.select_qemu_acceleration("auto")

        self.assertEqual(qemu, "/usr/bin/qemu-system-x86_64")
        self.assertEqual(acceleration, "tcg")

    def test_selects_kvm_when_available_and_prefers_it_in_auto_mode(self) -> None:
        with patch.object(runner.os, "geteuid", return_value=4242), patch.object(
            runner.os, "access", return_value=True
        ), patch.object(
            runner.shutil, "which", return_value="/usr/bin/qemu-system-x86_64"
        ), patch.object(runner, "run", return_value=subprocess.CompletedProcess([], 0, stdout="tcg\nkvm\n", stderr="")):
            qemu, acceleration = runner.select_qemu_acceleration("auto")

        self.assertEqual(qemu, "/usr/bin/qemu-system-x86_64")
        self.assertEqual(acceleration, "kvm")

    def test_github_workflow_runs_tcg_fixture_if_kvm_is_unavailable(self) -> None:
        workflow = SCRIPT.parents[1] / ".github/workflows/vforge-source-tests.yml"
        contents = workflow.read_text(encoding="utf-8")

        self.assertIn("Run Vforge rootless lifecycle with the built OCI artifact in QEMU/TCG", contents)
        self.assertIn("if: steps.kvm.outputs.available != 'true'", contents)
        self.assertIn("--acceleration tcg --timeout 5400", contents)
        self.assertIn("steps.vforge_tcg.outcome", contents)
        self.assertNotIn("was skipped because this GitHub runner has no accessible KVM", contents)

    def test_mkosi_exports_the_manifest_consumed_by_the_guest_runner(self) -> None:
        root = SCRIPT.parents[1]
        config = (root / "mkosi.conf").read_text(encoding="utf-8")
        workflow = (root / ".github/workflows/vforge-source-tests.yml").read_text(encoding="utf-8")

        self.assertIn("ManifestFormat=json", config)
        self.assertIn("scripts/check-image.py mkosi.output/veyra-next.raw", workflow)
        self.assertIn("mkosi.output/veyra-next.manifest", workflow)

    def test_guest_fixture_loads_extensionless_vforge_executable_for_runtime_identity(self) -> None:
        with patch.object(fixture.shutil, "which", return_value=str(SCRIPT.parents[1] / "mkosi.extra/usr/bin/vforge")):
            module = fixture.load_vforge_module()

        self.assertTrue(callable(module.validate_container_root_marker))
        self.assertTrue(callable(module.common_state_directory))

    def test_veyra_lower_fixture_requires_erofs_root(self) -> None:
        fixture.require_veyra_ero_root("erofs")
        with self.assertRaisesRegex(RuntimeError, "current EROFS Veyra lower"):
            fixture.require_veyra_ero_root("ext4")


if __name__ == "__main__":
    unittest.main()
