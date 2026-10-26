from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONFIGURE = ROOT / "mkosi.configure"


class MkosiConfigureTests(unittest.TestCase):
    def run_configure(self, config: dict[str, object]) -> dict[str, object]:
        result = subprocess.run(
            [sys.executable, str(CONFIGURE)], input=json.dumps(config), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if result.returncode:
            raise RuntimeError(result.stderr)
        return json.loads(result.stdout)

    def test_main_image_embeds_its_version_in_the_uki_command_line(self) -> None:
        config = self.run_configure({
            "image_id": "veyra-next",
            "image_version": "1.2.3-test",
            "kernel_command_line": ["systemd.unit=graphical.target"],
        })
        self.assertEqual(
            config["kernel_command_line"],
            ["systemd.unit=graphical.target", "veyra.image_version=1.2.3-test"],
        )
        self.assertEqual(config["unified_kernel_image_format"], "veyra-next_1.2.3-test")

    def test_conflicting_existing_image_version_parameter_is_rejected(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CONFIGURE)],
            input=json.dumps({
                "image_id": "veyra-next",
                "image_version": "1.2.3",
                "kernel_command_line": ["veyra.image_version=1.2.2"],
            }),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts with ImageVersion", result.stderr)

    def test_unbounded_image_version_is_rejected_before_uki_construction(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CONFIGURE)],
            input=json.dumps({
                "image_id": "veyra-next",
                "image_version": "v" * 65,
                "kernel_command_line": [],
            }),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe ImageVersion", result.stderr)

    def test_missing_image_version_is_rejected_for_the_system_image(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CONFIGURE)],
            input=json.dumps({
                "image_id": "veyra-next",
                "kernel_command_line": [],
            }),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsafe ImageVersion", result.stderr)


if __name__ == "__main__":
    unittest.main()
