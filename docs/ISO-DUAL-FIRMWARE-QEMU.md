# Dual-firmware Live ISO diagnostic: BIOS and UEFI QEMU

The current Prototype ISO pipeline is **not yet certified to boot and install**
on real or virtual machines. Building an ISO and discovering both El Torito
boot records is insufficient. The test in `scripts/test-live-iso-qemu.py`
runs the same generated ISO under **SeaBIOS** and **UEFI/OVMF** and records
guest screens at specified times. It does not invent a boot-success signal.

## Run it after building the ISO on a Linux machine

Requires Python 3, `qemu-system-x86_64`, and OVMF firmware for UEFI.
No KVM, loop devices, elevated privileges, network, or writable target
disk are needed. It uses TCG software emulation.

```sh
python3 scripts/test-live-iso-qemu.py \
  --iso build/live-iso/veyra-next-prototype-live.iso \
  --output build/iso-qemu \
  --modes both \
  --at 20,60
```

The ISO is passed as a **read-only optical drive**; no writable guest hard
disk is attached, and the guest NIC is disabled. UEFI variable storage is
copied to a disposable temporary directory. QEMU is terminated after the
last screenshot, so it cannot continue modifying host resources.

Outputs, per firmware mode:
- `screen-020.png` and `screen-060.png`: actual QEMU VGA screenshots.
- `serial.log`: kernel/early userspace output if the Live kernel enables
  serial console; absence of serial output alone is not a failure.
- `qemu.stderr`, `qemu.stdout`: emulator diagnostics.
- `report.json`: modes, screenshot sizes, weak serial hints, errors.

**Limitations:** a black screen, firmware logo, boot menu, installer prompt,
or login screen are all possible. A captured PNG is never proof of a
successful boot. The next required gate is manual inspection of both firmware
screenshots, then an interactive guest run of the KDE Live desktop and an
installation to an additional **disposable** virtual disk of at least 56 GiB.
This helper deliberately does **not** attempt destructive installation.

The source unit tests cover both firmware command lines, read-only media,
transient UEFI variables, PNG correctness, timeout input and report safety.
After the ISO pipeline passes, QEMU reports are published as a short-lived
Actions artifact. Source tests do not require a built ISO.
