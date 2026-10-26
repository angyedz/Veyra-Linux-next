# Veyra Next: UEFI and Legacy BIOS support

## Current state

The image is configured to boot through ordinary UEFI and Legacy BIOS/CSM.
UEFI keeps systemd-boot and unsigned UKIs. BIOS uses GRUB's `i386-pc`
boot code and the conventional kernel/initramfs. CI now has a SeaBIOS QEMU
gate that requires the guest to reach `graphical.target`; its result is
pending until the updated workflow completes.

This boot path does not yet qualify BIOS OTA activation, rollback, or
Recovery. A BIOS image must not be released as supported until those paths
work. Secure Boot is also unsupported.

## Boot design

- **UEFI without Secure Boot:** systemd-boot loads the unsigned UKI.
- **Legacy BIOS / CSM:** GRUB's `i386-pc` boot code is installed into the
  disk image. GPT contains an unformatted 1 MiB BIOS Boot Partition
  (type EF02 / `21686148-6449-6E6F-744E-656564454649`) and an EFI System
  Partition for the UEFI path.
- **BIOS kernel boot:** GRUB loads the conventional kernel and initramfs
  copied to the ESP. The EFI UKI remains the UEFI boot artifact.
- **QEMU acceptance:** `scripts/test-bios-boot.py` runs SeaBIOS with a
  temporary disk snapshot and succeeds only when a CI-only service emits
  its marker after `graphical.target`. A timeout or early exit fails.
- **A/B updates and Recovery:** UEFI's update and rollback path continues
  to use systemd-boot. BIOS-specific update selection, rollback, and
  Recovery must be implemented and tested before claiming parity.

## Diagnostics

After building an image, run this on a Linux host with QEMU and `sfdisk`:

```sh
bash scripts/qemu-bios-smoke.sh mkosi.output/veyra-next.raw qemu-bios-report
```

The test writes QEMU and serial logs under the report directory and does
not modify the source image.

## Remaining BIOS support gates

1. Validate BIOS OTA activation and rollback against the same A/B root
   generation and verified-root metadata used by UEFI.
2. Add a BIOS Recovery entry and verify it survives failed OTA.
3. Test installation, firmware detection, and bootloader repair in
   disposable BIOS and UEFI VMs.

Secure Boot stays disabled until Veyra has a reviewed signing and key
enrollment design.
