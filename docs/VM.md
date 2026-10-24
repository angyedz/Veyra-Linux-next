# Build and boot a Veyra image in a VM

This recipe builds an experimental disk image. Keep the VM disk disposable; the image uses KDE Plasma Setup to create the initial account.

## Requirements

- Linux kernel 5.12 or newer and systemd 254 or newer.
- `uv`, `systemd-nspawn`, user namespaces and the host packages returned by `scripts/mkosi.sh dependencies`.
- QEMU and UEFI firmware for VM booting.

## Build

```sh
scripts/mkosi.sh dependencies
scripts/mkosi.sh --force build
```

mkosi places the image under `mkosi.output/`.

## Boot

```sh
scripts/mkosi.sh vm --console=gui
```

The first boot should show KDE Plasma Setup before Plasma Login Manager. Complete account creation in the disposable VM to test the KDE desktop. Do not publish this image as something users can install: it still has no installer, A/B updates, verified boot, automatic rollback or Recovery.

This pre-alpha image has unsigned UKIs. mkosi is configured to use ordinary UEFI firmware in its VM; Secure Boot remains disabled until Veyra has a release signing and key-enrollment design.

## What this test proves

A VM boot can prove that the disk layout, kernel, initramfs, systemd, first-boot setup and Plasma Login Manager start together in that virtual machine. It does not prove real GPU support, screen sharing, Proton game compatibility, automatic rollback or Secure Boot.
