# Veyra Next: UEFI and Legacy BIOS support

## Current state

**UEFI only is implemented.** The mkosi image uses systemd-boot and
unsigned UKIs. Legacy BIOS and Secure Boot are **not** implemented by
the presence of a GRUB theme file. Do not advertise either as supported.

## Compatibility plan

- **UEFI without Secure Boot:** retain systemd-boot and UKIs. This is
  the current prototype's boot path.
- **UEFI with Secure Boot:** require a signed, verified UKI and a trusted
  signing/enrollment chain. Do not enable until signing and key lifecycle
  have been reviewed.
- **Legacy BIOS / CSM:** use GRUB's `i386-pc` target. On a GPT disk,
  reserve an unformatted BIOS Boot Partition (GPT type EF02 /
  `21686148-6449-6E6F-744E-656564454649`), and install GRUB's core
  image to the disk boot area. An EFI System Partition alone cannot
  satisfy this requirement.
- **BIOS kernel boot:** GRUB must load a conventional Linux kernel and
  initramfs. An EFI UKI is not directly BIOS-bootable. The build must
  publish and maintain BIOS-compatible kernel/initramfs artifacts,
  synchronized with each system update and rollback generation.
- **A/B updates:** preserve the firmware-independent slot metadata and
  root verification model. BIOS GRUB configuration must select the same
  active/pending slot as UEFI, support rollback, and must not allow a
  successful boot to be reported before the selected root is verified.
- **Recovery:** create BIOS GRUB recovery entry with an independently
  bootable recovery kernel/initramfs, and verify it survives failed OTA.
- **Disk layout:** any partition-table change must be reviewed with the
  image builder and OTA agent. Never repartition existing user disks as
  an automatic migration.

## Diagnostics

After building a disk image, on a Linux test host with QEMU:

```sh
bash scripts/qemu-bios-smoke.sh mkosi.output/veyra-next.raw qemu-bios-report
```

This runs SeaBIOS with a temporary QEMU snapshot and saves serial
output and stderr. It does not modify the original image. A QEMU
timeout is **not evidence of a successful boot**. The current UEFI
image is expected **not** to boot under SeaBIOS.

## Acceptance gates before calling Legacy BIOS supported

1. Build reproducible GPT image with BIOS Boot Partition, EFI System
   Partition, and verified root layout.
2. Install `grub2-pc` BIOS boot code to a test disk image, not the
   CI runner's host disk.
3. Boot the same build under both SeaBIOS and OVMF in QEMU.
4. Verify actual kernel/systemd startup, graphical login, Recovery,
   OTA activation and rollback in both firmware modes.
5. Test both BIOS and UEFI install paths in a disposable VM, and
   document firmware detection and bootloader repair.
