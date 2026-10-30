"""Offline Lorax retry-policy tests: mocked builder, no disks or internet."""
from __future__ import annotations
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class LiveIsoRetryTests(unittest.TestCase):
    def test_legacy_lorax_is_explicitly_manual_and_has_both_uefi_assets(self):
        workflow = (ROOT / ".github/workflows/prototype-iso-unblock.yml").read_text()
        kickstart = (ROOT / "installer/veyra-live.ks.in").read_text()
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("recovery_run_id:", workflow)
        self.assertIn("run-id: ${{ inputs.recovery_run_id }}", workflow)
        self.assertIn("github.event_name == 'workflow_dispatch'", workflow)
        self.assertNotIn("40362375406", workflow)
        self.assertIn("shim-x64", kickstart)
        self.assertIn("shimx64.efi", kickstart)
        self.assertIn("gcdx64.efi", kickstart)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "installer").mkdir()
        (self.root / "build/recovery-uki").mkdir(parents=True)
        (self.root / "mock-bin").mkdir()
        shutil.copy2(ROOT / "scripts/build-live-iso.sh", self.root / "scripts/build-live-iso.sh")
        (self.root / "installer/veyra-live.ks.in").write_text("source=__VEYRA_SOURCE_ROOT__\n")
        (self.root / "build/recovery-uki/veyra-recovery.efi").write_bytes(b"fixture UKI")
        (self.root / "fedora-boot.iso").write_bytes(b"fixture netinst")
        self.counter = self.root / "counter"
        mock_lorax = r'''#!/usr/bin/env bash
set -euo pipefail
result=""
log=""
for arg in "$@"; do
  case "$arg" in
    --resultdir=*) result="${arg#*=}" ;;
    --logfile=*) log="${arg#*=}" ;;
  esac
done
n=0
if [[ -f "$MOCK_COUNT" ]]; then n="$(cat "$MOCK_COUNT")"; fi
n=$((n + 1))
echo "$n" > "$MOCK_COUNT"
if [[ "$MOCK_FAIL_MODE" = "first-console-network" && "$n" = 1 ]]; then
  echo "Failed to download packages: curl error from mirror" >&2
  echo "Image creation failed" > "$log"
  exit 1
fi
if [[ "$MOCK_FAIL_MODE" = "first-network" && "$n" = 1 ]] || [[ "$MOCK_FAIL_MODE" = "always-network" ]]; then
  echo "Failed to download the following packages: Failed to download packages." > "$log"
  exit 1
fi
if [[ "$MOCK_FAIL_MODE" = "other" ]]; then
  echo "Lorax failed: cannot generate EFI boot image" > "$log"
  exit 1
fi
mkdir -p "$result"
printf 'mock ISO only\n' > "$result/veyra-next-prototype-live.iso"
echo "successful image fixture" > "$log"
'''
        mock_xorriso = """#!/usr/bin/env bash
printf '%s\\n' '-b images/eltorito.img -e images/efiboot.img'
"""
        mock_implant = """#!/usr/bin/env bash
set -euo pipefail
printf 'embedded-media-checksum\\n' >> "$1"
"""
        mock_check = """#!/usr/bin/env bash
set -euo pipefail
grep -Fq embedded-media-checksum "$1"
"""
        for name, value in (
            ("livemedia-creator", mock_lorax), ("xorriso", mock_xorriso),
            ("implantisomd5", mock_implant), ("checkisomd5", mock_check),
        ):
            path = self.root / "mock-bin" / name
            path.write_text(value)
            path.chmod(0o755)

    def run_builder(self, mode, max_attempts="2"):
        env = dict(os.environ)
        env.update({
            "VEYRA_FEDORA_BOOT_ISO": str(self.root / "fedora-boot.iso"),
            "VEYRA_ISO_MAX_ATTEMPTS": max_attempts,
            "MOCK_FAIL_MODE": mode,
            "MOCK_COUNT": str(self.counter),
            "PATH": str(self.root / "mock-bin") + os.pathsep + os.environ["PATH"],
        })
        return subprocess.run(
            ["bash", str(self.root / "scripts/build-live-iso.sh")],
            env=env, cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=20,
        )

    def test_kickstart_has_bounded_anaconda_download_retries(self):
        kickstart = (ROOT / "installer/veyra-live.ks.in").read_text()
        package_lines = [line.strip() for line in kickstart.splitlines()
                         if line.startswith("%packages")]
        self.assertEqual(len(package_lines), 1)
        self.assertIn("--timeout=120", package_lines[0])
        self.assertIn("--retries=20", package_lines[0])
        self.assertIn("--excludedocs", package_lines[0])

    def test_download_failure_retries_once_and_keeps_log(self):
        result = self.run_builder("first-network")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.counter.read_text().strip(), "2")
        self.assertTrue((self.root / "build/iso-attempt-logs/livemedia-attempt-1.log").is_file())
        self.assertTrue((self.root / "build/live-iso/veyra-next-prototype-live.iso").is_file())
        self.assertTrue((self.root / "build/live-iso/veyra-next-prototype-live.iso.sha256").is_file())
        # Checksum must be implanted and checked before the published SHA256.
        iso = self.root / "build/live-iso/veyra-next-prototype-live.iso"
        self.assertIn(b"embedded-media-checksum", iso.read_bytes())
        self.assertEqual(
            (self.root / "build/live-iso/veyra-next-prototype-live.iso.sha256").read_text().split()[0],
            __import__("hashlib").sha256(iso.read_bytes()).hexdigest(),
        )

    def test_console_only_failure_is_detected_and_preserved(self):
        result = self.run_builder("first-console-network")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.counter.read_text().strip(), "2")
        console = self.root / "build/iso-attempt-logs/livemedia-attempt-1-console.log"
        self.assertIn("curl error", console.read_text())

    def test_unrelated_iso_failure_does_not_retry(self):
        result = self.run_builder("other")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.counter.read_text().strip(), "1")
        self.assertTrue((self.root / "build/iso-attempt-logs/livemedia-attempt-1.log").is_file())

    def test_retry_budget_is_respected(self):
        result = self.run_builder("always-network", max_attempts="2")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.counter.read_text().strip(), "2")
        self.assertTrue((self.root / "build/iso-attempt-logs/livemedia-attempt-2.log").is_file())

    def test_invalid_budget_rejected_before_builder(self):
        result = self.run_builder("first-network", max_attempts="99")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.counter.exists())


if __name__ == "__main__":
    unittest.main()
