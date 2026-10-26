# Veyra Next: UEFI and Legacy BIOS support

## Current state

The image is configured to boot through ordinary UEFI and Legacy BIOS/CSM.
UEFI keeps systemd-boot and unsigned UKIs. BIOS uses GRUB's `i386-pc`
boot code and the conventional kernel/initramfs. CI now has a SeaBIOS QEMU
gate that requires the guest to reach `graphical.target`; its result is
pending until the updated workflow completes.

The BIOS updater path is implemented in `veyra-update` and GRUB state handling,
but it is not yet qualified: the current QEMU test checks the initial BIOS
boot only. BIOS OTA activation, blessing, and rollback need an end-to-end
SeaBIOS test, and Recovery remains unimplemented. Do not advertise BIOS updates
or Recovery as supported until their gates pass. Secure Boot is also
unsupported.

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
- **A/B updates:** UEFI uses systemd-boot. BIOS has a GRUB trial counter,
  blessing, and fallback implementation; full OTA behavior still needs to pass
  under SeaBIOS before claiming parity.
- **Recovery:** BIOS and UEFI Recovery entries are not yet implemented or
  tested.

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
