# Diagnostic QEMU boot checks for Veyra Next Live Installer ISO

This prototype is an optical ISO, not the installed A/B disk image.
Test the ISO in both firmware modes **without touching the computer's disks**.

On an isolated Linux CI runner or disposable VM with `qemu-system-x86_64`,
`xorriso`, GNU `timeout`, and `ovmf` installed:

```sh
bash scripts/qemu-live-iso-smoke.sh \
  build/live-iso/veyra-next-prototype-live.iso \
  build/qemu-live-boot \
  both
```

The script:
- requires BIOS and EFI El Torito boot entries before starting any guests;
- attaches only the immutable CD-ROM ISO (`readonly=on`) and no writable disks;
- boots once with SeaBIOS, then once with a fresh copy of OVMF variables;
- uses QEMU TCG (no KVM/host state dependency) and no networking;
- writes firmware-specific serial logs, exit codes and command output;
- reports panic/root mount errors and labels missing kernel evidence **UNVERIFIED**.

A QEMU timeout can mean the VM is running, blocked in firmware, stuck at
GRUB, or failed later in live userspace. The script does **not** claim a
graphical desktop, Calamares installer, installation, or OTA succeeded.
A separate interactive GUI QEMU test must confirm KDE and install the OS
onto an explicitly created disposable virtual disk before closing PR #23.

This helper is isolated on a stacked branch and does not run against the
user's real hardware.
