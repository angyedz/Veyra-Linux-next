# Build and boot a Veyra image in a VM

This recipe builds an experimental image with two fixed-size root slots. Keep the VM disk disposable; the image uses KDE Plasma Setup to create the initial account. The virtual disk should be at least 64 GiB for the current partition sizes.

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

The first boot should mount the persistent `/etc`, `/var` and `/home` partitions, then show KDE Plasma Setup before Plasma Login Manager. Complete account creation in the disposable VM to test the KDE desktop. CI already applies a temporary signed A→B update, boots the new slot, and verifies fallback after three failed trial boots. That test uses a disposable signing key; Veyra still has no production update key or public update release. This image still has no installer or Recovery.

This pre-alpha image has unsigned UKIs. mkosi is configured to use ordinary UEFI firmware in its VM; Secure Boot remains disabled until Veyra has a boot-artifact signing and key-enrollment design.

## What this test proves

A manual VM boot can prove that the disk layout, kernel, initramfs, systemd, first-boot setup and Plasma Login Manager start together in that virtual machine. The CI A/B test runs the signed OTA updater inside a disposable QEMU guest, checks `/etc`, `/var` and `/home` across the slot change, exercises signed `vpkg` install/upgrade/remove and activation-failure rollback through the real `systemd-sysext`, and tests boot-count fallback. CI does not prove real GPU support, screen sharing, Proton game compatibility or Secure Boot.
