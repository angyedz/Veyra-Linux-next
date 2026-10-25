from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import unittest


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts/test-vforge-integration-qemu.py"
SPEC = importlib.util.spec_from_file_location("vforge_qemu_fixture", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
fixture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixture)


class EnospcProbeTests(unittest.TestCase):
    def test_accepts_only_successful_guest_probe_with_enospc(self) -> None:
        result = subprocess.CompletedProcess(
            args=["vforge", "exec"],
            returncode=0,
            stdout="dd: error writing '/usr/share/probe': No space left on device\nVFORGE_ENOSPC_PROBE_EXECUTED\n",
            stderr="",
        )

        fixture.require_enospc_probe(result)

    def test_rejects_exec_failure_even_when_it_mentions_enospc(self) -> None:
        result = subprocess.CompletedProcess(
            args=["vforge", "exec"],
            returncode=125,
            stdout="",
            stderr="container runtime: No space left on device\n",
        )

        with self.assertRaisesRegex(RuntimeError, "probe command did not complete"):
            fixture.require_enospc_probe(result)

    def test_rejects_guest_command_that_did_not_reach_its_sentinel(self) -> None:
        result = subprocess.CompletedProcess(
            args=["vforge", "exec"],
            returncode=0,
            stdout="dd: No space left on device\n",
            stderr="",
        )

        with self.assertRaisesRegex(RuntimeError, "did not reach the guest shell"):
            fixture.require_enospc_probe(result)

    def test_rejects_non_enospc_guest_write_failure(self) -> None:
        result = subprocess.CompletedProcess(
            args=["vforge", "exec"],
            returncode=0,
            stdout="dd: Permission denied\nVFORGE_ENOSPC_PROBE_EXECUTED\n",
            stderr="",
        )

        with self.assertRaisesRegex(RuntimeError, "did not fail with ENOSPC"):
            fixture.require_enospc_probe(result)


if __name__ == "__main__":
    unittest.main()
