from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "veyra_recovery", ROOT / "scripts" / "veyra_recovery.py"
)
assert SPEC is not None and SPEC.loader is not None
recovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(recovery)


class BootEntryTests(unittest.TestCase):
    def test_parses_bootctl_short_json_entries(self) -> None:
        entries = recovery.parse_boot_entries(
            '{"type":"section","entries":[{"id":"veyra-next_0.1.0.efi",'
            '"title":"Veyra 0.1.0"}]}'
        )
        self.assertEqual(entries[0]["id"], "veyra-next_0.1.0.efi")

    def test_rejects_malformed_bootctl_json(self) -> None:
        with self.assertRaisesRegex(recovery.RecoveryError, "valid JSON"):
            recovery.parse_boot_entries("not-json")

    def test_rejects_unknown_bootctl_shape(self) -> None:
        with self.assertRaisesRegex(recovery.RecoveryError, "entry list"):
            recovery.parse_boot_entries('{"entries":"not-a-list"}')

    def test_previous_candidates_are_only_blessed_veyra_entries(self) -> None:
        entries = [
            {"id": "veyra-next_0.2.0+2-0.efi", "title": "Veyra trial", "tries_left": 2},
            {"id": "veyra-next_0.1.0.efi", "title": "Veyra 0.1.0"},
            {"id": "fedora-44.conf", "title": "Fedora"},
            {"id": "veyra-next_0.0.9.efi", "title": "Veyra 0.0.9", "current": True},
        ]
        candidates = recovery.previous_boot_candidates(entries)
        self.assertEqual([entry["id"] for entry in candidates], ["veyra-next_0.1.0.efi"])

    def test_rejects_trial_entries_even_without_try_metadata(self) -> None:
        entries = [{"id": "veyra-next_0.2.0+3-0.efi", "title": "Veyra trial"}]
        self.assertEqual(recovery.previous_boot_candidates(entries), [])

    def test_does_not_offer_current_entry_as_rollback(self) -> None:
        entries = [{"id": "veyra-next_0.1.0.efi", "title": "Veyra", "current": True}]
        self.assertEqual(recovery.previous_boot_candidates(entries), [])

    def test_selection_only_sets_one_shot_after_exact_confirmation(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command, **_kwargs):
            commands.append(list(command))
            if command[0] == "bootctl" and command[-1] == "list":
                return subprocess.CompletedProcess(command, 0, '{"entries":['
                    '{"id":"veyra-next_0.1.0.efi","title":"Previous Veyra"}]}' , "")
            return subprocess.CompletedProcess(command, 0, "", "")

        answers = iter(["1", "BOOT"])
        selected = recovery.select_previous_deployment(
            pathlib.Path("/mounted/esp"), fake_run, lambda _prompt: next(answers)
        )

        self.assertTrue(selected)
        self.assertIn(
            ["bootctl", "--esp-path=/mounted/esp", "set-oneshot", "veyra-next_0.1.0.efi"],
            commands,
        )
        self.assertEqual(commands[-1], ["sync"])

    def test_cancelled_selection_does_not_touch_boot_variables(self) -> None:
        commands: list[list[str]] = []

        def fake_run(command, **_kwargs):
            commands.append(list(command))
            return subprocess.CompletedProcess(
                command, 0,
                '{"entries":[{"id":"veyra-next_0.1.0.efi","title":"Previous Veyra"}]}'
                if command[-1] == "list" else "", "",
            )

        answers = iter(["1", "no"])
        selected = recovery.select_previous_deployment(
            pathlib.Path("/mounted/esp"), fake_run, lambda _prompt: next(answers)
        )

        self.assertFalse(selected)
        self.assertFalse(any("set-oneshot" in command for command in commands))

    def test_requires_exactly_one_gpt_efi_partition(self) -> None:
        class FakeResult:
            returncode = 0
            stderr = ""
            stdout = '{"blockdevices":[{"path":"/dev/vda1","type":"part",' \
                '"parttype":"c12a7328-f81f-11d2-ba4b-00a0c93ec93b"}]}'

        self.assertEqual(recovery.find_esp_device(lambda *_args, **_kwargs: FakeResult()), "/dev/vda1")

        FakeResult.stdout = '{"blockdevices":[]}'
        with self.assertRaisesRegex(recovery.RecoveryError, "exactly one"):
            recovery.find_esp_device(lambda *_args, **_kwargs: FakeResult())


if __name__ == "__main__":
    unittest.main()
