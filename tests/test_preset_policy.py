"""Guard against false-positive systemd preset checks in image smoke tests."""
from __future__ import annotations

import importlib.util
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("smoke", ROOT / "scripts/smoke.py")
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class PresetValidationTests(unittest.TestCase):
    def test_current_preset_passes(self):
        self.assertEqual(smoke.main(), 0)

    def test_disabled_service_is_not_accepted_as_enabled(self):
        original = (ROOT / "mkosi.extra/usr/lib/systemd/system-preset/99-veyra.preset").read_text()
        with tempfile.TemporaryDirectory() as temp:
            fake_root = pathlib.Path(temp)
            fake_preset = fake_root / "mkosi.extra/usr/lib/systemd/system-preset/99-veyra.preset"
            fake_preset.parent.mkdir(parents=True)
            fake_preset.write_text(original.replace(
                "enable NetworkManager.service", "disable NetworkManager.service"
            ), encoding="utf-8")
            with patch.object(smoke, "ROOT", fake_root):
                original_read = pathlib.Path.read_text
                def redirected_read(path, *args, **kwargs):
                    if path == fake_preset:
                        return original_read(path, *args, **kwargs)
                    actual = ROOT / path.relative_to(fake_root)
                    return original_read(actual, *args, **kwargs)
                with patch.object(pathlib.Path, "read_text", redirected_read):
                    self.assertEqual(smoke.main(), 1)


if __name__ == "__main__":
    unittest.main()
