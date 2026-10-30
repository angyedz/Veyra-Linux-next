#!/usr/bin/env python3
"""Require late Plasma-process, framebuffer, vpkg and installer-preflight evidence in BIOS/UEFI."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def verify(report: dict) -> list[str]:
    issues = []
    if not report.get("cdrom_readonly") or not report.get("network_disabled") or not report.get("efi_variables_temporary"):
        issues.append("QEMU did not report expected read-only/offline/disposable safeguards")
    modes = report.get("modes")
    if not isinstance(modes, list) or len(modes) != 2:
        return issues + ["both firmware modes must be tested"]
    seen = set()
    for item in modes:
        if not isinstance(item, dict):
            issues.append("invalid firmware result")
            continue
        mode = item.get("mode")
        if mode not in ("bios", "uefi") or mode in seen:
            issues.append(f"unexpected or repeated firmware: {mode!r}")
            continue
        seen.add(mode)
        if item.get("errors"):
            issues.append(f"{mode}: QEMU capture failed: {item['errors']!r}")
        screenshots = item.get("screenshots")
        if not screenshots:
            issues.append(f"{mode}: no guest screenshot recorded")
        elif not isinstance(screenshots, list) or not isinstance(screenshots[-1], dict):
            issues.append(f"{mode}: final screenshot evidence is malformed")
        elif screenshots[-1].get("non_black_pixels_detected") is not True:
            issues.append(f"{mode}: final screenshot has no confirmed visible framebuffer (black or unchecked)")
        elif not isinstance(screenshots[-1].get("seconds_after_launch"), int) or screenshots[-1]["seconds_after_launch"] < 300:
            issues.append(f"{mode}: final screenshot was captured too early to prove Live Plasma rendered")
        signals = item.get("serial_signals", {})
        if not isinstance(signals, dict) or not signals.get("kernel_seen"):
            issues.append(f"{mode}: no kernel startup evidence in serial output")
        if not isinstance(signals, dict) or not signals.get("init_seen"):
            issues.append(f"{mode}: no init/dracut startup evidence in serial output")
        if not isinstance(signals, dict) or not signals.get("plasma_runtime_confirmed"):
            issues.append(f"{mode}: did not confirm liveuser KWin and plasmashell processes")
        if isinstance(signals, dict) and signals.get("plasma_runtime_failed"):
            issues.append(f"{mode}: Plasma process probe reported failure")
        if isinstance(signals, dict) and signals.get("ksplash_timed_out"):
            issues.append(f"{mode}: KDE splash screen timed out")
        if isinstance(signals, dict) and signals.get("milou_qml_missing"):
            issues.append(f"{mode}: missing KDE plasma-milou QML module")
        if isinstance(signals, dict) and signals.get("plasma_session_crashed"):
            issues.append(f"{mode}: SDDM reports a crashed Plasma session")
        if isinstance(signals, dict) and signals.get("pam_systemd_missing"):
            issues.append(f"{mode}: systemd-pam is absent or broken")
        if not isinstance(signals, dict) or not signals.get("vpkg_guest_test_passed"):
            issues.append(f"{mode}: Live vpkg QEMU policy and privilege checks did not pass")
        if not isinstance(signals, dict) or not signals.get("installer_preflight_passed"):
            issues.append(f"{mode}: installer read-only preflight did not pass (with Recovery or fail-closed without it)")
        if isinstance(signals, dict) and signals.get("vpkg_guest_test_failed"):
            issues.append(f"{mode}: Live vpkg QEMU policy suite reported a failure")
    if seen != {"bios", "uefi"}:
        issues.append("BIOS and UEFI must both be represented")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"QEMU evidence unavailable: {exc}", file=sys.stderr)
        return 2
    problems = verify(report)
    for problem in problems:
        print(f"Live ISO boot check: {problem}", file=sys.stderr)
    if problems:
        return 1
    print("BIOS and UEFI both showed late framebuffer activity, Plasma processes and guest policy/preflight evidence.")
    print("Note: this does NOT verify interactive desktop functionality or a completed Calamares installation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
