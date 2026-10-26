# Veyra Next: prototype ISO and installer handoff — 26 October 2026

## Stacked branches: keep this order

1. Draft PR #23 `prototype/local-tryout` → `main`: Live Installer ISO,
   KDE/Calamares, package-based installation to A/B/verity target.
2. Draft PR #24 `fix/iso-uefi-cdboot-resume` → PR #23: missing Fedora
   `grub2-efi-x64-cdboot` package and an early UEFI bootloader check.
3. Draft PR #25 `fix/installer-preflight-and-disk-recheck` → PR #24:
   disk safety, asset preflight, identity recheck after typed confirmation.
4. Draft PR #26 `test/prototype-iso-dual-firmware-qemu` → PR #25:
   BIOS/UEFI QEMU observations, bounded network retries, CI and docs.
   This is the **continuation branch**. Rebase only deliberately; do
   not apply PR #26 directly to main without its parents.

Existing main, agent-owned branches, kernel, host bootloader and local user
disks were not changed in PR #26.

## Evidence and debugging

- CI run `40372875288` reached xorriso and failed because Lorax could
  not find `EFI/BOOT`. PR #24 adds `grub2-efi-x64-cdboot`.
- CI run `40399005173` passed Fedora signature verification and
  proceeded to Anaconda; it downloaded **1.14 GiB of 3.29 GiB**
  (2094 RPMs), then failed downloading packages. This is an upstream
  RPM/mirror transfer failure, not evidence of successful UEFI boot.
- PR #26 now supplies `%packages --timeout=120 --retries=20`;
  `build-live-iso.sh` retries Lorax **once only** after a recognized
  download failure; unrelated failures exit immediately. Logs are kept
  in `build/iso-attempt-logs` and CI artifacts.
- The `test-live-iso-qemu.py` script takes BIOS and UEFI screenshots
  using SeaBIOS/OVMF and records serial, stdout and stderr. It only
  examines a read-only CD-ROM and uses temporary EFI variables; it
  **does not assert success just because QEMU starts**.
- Offline CI `Prototype Live ISO QEMU harness checks` covers both
  command lines, PNG conversion, ISO builder retry behavior, log retention,
  failure paths and command safety. `Installer safety unit tests` and
  `Vforge native source checks` also pass on PR #26 as of this handoff.

## Next commands once the ISO job publishes an artifact

```sh
# Download from the successful Actions run (gh must have repository access).
gh run download RUN_ID --repo angyedz/Veyra-Linux-next \
  --name veyra-next-prototype-iso-unblock \
  --dir /tmp/veyra-prototype
cd /tmp/veyra-prototype
sha256sum --check veyra-next-prototype-live.iso.sha256
python3 /path/to/Veyra-Linux-next/scripts/test-live-iso-qemu.py \
  --iso ./veyra-next-prototype-live.iso \
  --output ./iso-qemu-report --modes both --at 20,60
```

Then inspect PNGs and guest serial logs for both firmware paths.
Next test an interactive Plasma Live session/Calamares GUI, followed by
an actual install into a **disposable QEMU disk of at least 56 GiB**.
Only after that: installed BIOS/UEFI boot, firstboot, Recovery, A/B
OTA and rollback under disk-failure conditions. Keep PRs Draft until
these steps are verified with artifacts, timestamps and exact SHAs.

A successful `livemedia-creator` run and ISO El Torito check are
**necessary but not sufficient** to call the installer functional.
