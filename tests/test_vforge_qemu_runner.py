from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/run-vforge-qemu.py"
SPEC = importlib.util.spec_from_file_location("vforge_qemu_runner", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


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

    def test_guest_boot_runner_is_valid_shell_and_uses_ro_source_as_normal_user(self) -> None:
        script = runner.test_runner_script()

        result = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True, check=False)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("version=9p2000.L,ro vforge-source", script)
        self.assertIn("runuser --user vforge-test", script)
        self.assertIn(runner.PASS_MARKER, script)
        self.assertIn(runner.FAIL_MARKER, script)

    def test_qemu_command_requires_kvm_and_mounts_checkout_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary)
            code = root / "OVMF_CODE.fd"
            variables = root / "OVMF_VARS.fd"
            variables_copy = root / "OVMF_VARS.copy.fd"
            for path in (code, variables):
                path.write_bytes(b"firmware")
            command = runner.qemu_command(
                "qemu-system-x86_64", root / "guest.raw", root / "checkout",
                code, variables, variables_copy, root / "serial.log",
            )

        self.assertIn("q35,accel=kvm", command)
        self.assertTrue(any("readonly=on" in item for item in command))
        self.assertTrue(any("security_model=none,readonly=on" in item for item in command))


if __name__ == "__main__":
    unittest.main()
