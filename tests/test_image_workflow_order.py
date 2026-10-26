"""Guard the expensive Fedora image workflow against late smoke checks."""
from __future__ import annotations

import pathlib
import unittest

WORKFLOW = pathlib.Path(__file__).resolve().parents[1] / ".github/workflows/ci.yml"


def validate_workflow(text: str) -> list[str]:
    errors = []
    smoke = "run: python3 scripts/smoke.py"
    install = "sudo apt-get install --yes"
    build = "scripts/mkosi.sh --force build"
    if text.count(smoke) != 1:
        errors.append("image smoke check must occur exactly once")
    if install not in text:
        errors.append("expected Fedora builder dependency install")
    if build not in text:
        errors.append("expected Fedora disk image build")
    if not errors and not (text.index(smoke) < text.index(install) < text.index(build)):
        errors.append("image smoke check must run before dependency installation and build")
    return errors


class ImageWorkflowOrderingTests(unittest.TestCase):
    def test_real_workflow(self):
        self.assertEqual(validate_workflow(WORKFLOW.read_text(encoding="utf-8")), [])

    def test_late_smoke_check_rejected(self):
        original = WORKFLOW.read_text(encoding="utf-8")
        bad = original.replace(
            "run: python3 scripts/smoke.py", "run: echo deferred", 1
        ).replace(
            "scripts/mkosi.sh --force build",
            "scripts/mkosi.sh --force build\n      - run: python3 scripts/smoke.py",
            1,
        )
        self.assertTrue(validate_workflow(bad))

    def test_missing_smoke_check_rejected(self):
        original = WORKFLOW.read_text(encoding="utf-8")
        self.assertTrue(validate_workflow(original.replace(
            "run: python3 scripts/smoke.py", "run: true", 1
        )))


if __name__ == "__main__":
    unittest.main()
