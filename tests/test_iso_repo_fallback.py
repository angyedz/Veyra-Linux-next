"""Offline checks for Fedora Live ISO repository retry configuration."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "scripts/build-live-iso.sh").read_text(encoding="utf-8")
KICKSTART = (ROOT / "installer/veyra-live.ks.in").read_text(encoding="utf-8")


class LiveIsoRepoFallbackTests(unittest.TestCase):
    def test_kickstart_is_parameterized(self):
        self.assertEqual(KICKSTART.count("url --url=__VEYRA_FEDORA_REPO_URL__"), 1)
        self.assertNotIn("url --url=https://", KICKSTART)
        self.assertIn("%packages", KICKSTART)
        self.assertIn("%end", KICKSTART)

    def test_distinct_official_fedora_repositories(self):
        primary = re.search(r'^repo_primary="([^"]+)"$', SCRIPT, re.M)
        fallback = re.search(r'^repo_fallback="([^"]+)"$', SCRIPT, re.M)
        self.assertIsNotNone(primary)
        self.assertIsNotNone(fallback)
        self.assertNotEqual(primary.group(1), fallback.group(1))
        self.assertTrue(primary.group(1).startswith("https://download.fedoraproject.org/"))
        self.assertTrue(fallback.group(1).startswith("https://dl.fedoraproject.org/"))
        self.assertTrue(primary.group(1).endswith("/44/Everything/x86_64/os/"))
        self.assertTrue(fallback.group(1).endswith("/44/Everything/x86_64/os/"))

    def test_retry_rewrites_kickstart_only_for_download_errors(self):
        self.assertIn('render_kickstart "$repo_url"', SCRIPT)
        self.assertIn('if ((attempt > 1)); then', SCRIPT)
        self.assertIn('repo_url="$repo_fallback"', SCRIPT)
        self.assertIn('if [[ ! "$max_attempts" =~ ^[12]$ ]]', SCRIPT)
        self.assertIn('Non-download Lorax error; will not retry', SCRIPT)
        self.assertIn('__VEYRA_FEDORA_REPO_URL__', SCRIPT)
        self.assertIn('--ks="$kickstart"', SCRIPT)


if __name__ == "__main__":
    unittest.main()
