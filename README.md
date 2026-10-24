# Veyra Next

![Veyra mark](branding/veyra-logo.svg)

Veyra Next is a new Fedora-based desktop OS project. The intended user experience is a normal KDE Plasma Wayland computer for gaming, communication and development. Fedora provides the package ecosystem and drivers; this repository defines Veyra's image and update behavior.

## Current stage

This repository builds its first Fedora 44 + KDE image with `mkosi`. It includes KDE Plasma Setup for first-boot account creation, Plasma Login Manager, Wayland, Flatpak/Discover, Flathub, PipeWire, Git and common native development tools. The disk recipe lays out two EROFS root slots with dm-verity data, plus separate persistent `/etc`, `/var` and `/home` partitions. The initrd mounts the writable state before switching to the installed system.

This is an image-building prototype, not a usable installer or pre-alpha release yet. `systemd-sysupdate` is configured to stage the matching verity partition and root into the inactive slot and install the UKI last with three boot attempts. A boot health check is enabled. CI and local builds validate the image layout, but the A→B transition and failed-boot rollback still need a VM test. Release publication and the trusted update-signing key are not configured, so there is no usable OS update channel yet. The first-boot account flow also needs VM validation. A graphical installer, Recovery and Developer Mode are not implemented. It has not been tested on real hardware. Steam/Proton, Discord screen sharing and NVIDIA/AMD compatibility are not yet qualified.

The root image is read-only EROFS and has dm-verity integrity data. Update transfers require signed `SHA256SUMS` manifests (`Verify=yes`), but a release signing key is not provisioned yet. The UKIs are unsigned, so the current image boots with Secure Boot disabled and does not authenticate the dm-verity root hash against a hardware trust root.

## Optional RPM packages

`vpkg` is included as the first package-management prototype. Its `main` and `testing` channels are repository directories, not Git branches. DNF5 resolves packages from the selected Veyra channel only, inside a separate writable installroot so it does not write to Fedora's RPM database in `/usr`. `vpkg` accepts only signed `veyra-*` RPMs whose payload is one system-extension image; it refuses ordinary RPM file trees and executable RPM scriptlets. It exposes that image under `/etc/extensions`, refreshes `systemd-sysext`, and attempts to undo the transaction if activation fails. Base-system, kernel, and boot changes remain A/B image updates.

The first optional package source is `veyra-shell-tools` (bash completion, fzf, jq and ripgrep). The packaging pipeline builds it as a version-matched system extension and wraps it in an RPM. Package channels require both RPM and repository-metadata signatures. There is no production key or published repository yet, so `vpkg` searches and installs are not available until the signing key and repository hosting are provisioned. See [package repository details](packages/README.md).

## Build

Requirements: Linux with systemd 254+, `uv`, user namespaces, and the dependencies reported by `scripts/mkosi.sh dependencies`. Allow a 64 GiB virtual disk for the current fixed-size A/B prototype. The builder itself is pinned to an upstream mkosi revision.

```sh
python3 scripts/smoke.py
scripts/mkosi.sh --force build
```

The disk image, versioned UKI, and compressed root/verity update artifacts are written to `mkosi.output/`. CI checks the GPT layout and artifact set. Boot the image in a disposable VM with:

```sh
scripts/mkosi.sh vm --console=gui
```

The image is configured to offer KDE Plasma Setup on first boot so the user can create an ordinary account before reaching Plasma. Verify that flow, both persistent state mounts, and an A→B update followed by a deliberately failed trial boot before treating the image as usable. The source currently has no signed release bundle or trusted update key, so the configured updater cannot install a published release yet.

## How the finished desktop should feel

- The user signs into KDE Plasma and installs desktop apps from Discover.
- Steam and Discord are ordinary desktop apps, not separate operating systems. Steam runs games with Proton; Discord receives Wayland screen sharing through KDE's portal and PipeWire.
- Git, SSH, GCC, Clang, Python, Node.js and CMake run directly in the native Fedora userspace. Project files live in the user's home directory. Toolbx/container use stays optional.
- System updates preserve personal files and install a complete new OS version beside the active one. A failed trial boot returns to the previous version.
- Optional Veyra add-ons install as signed system extensions, while system packages go through A/B image updates.

Those are product goals, not claims that this image already provides them.

## Architectural direction

Kinoite is a reference for the KDE Atomic user experience, not the implementation to rebrand. The proposed Veyra path is Fedora packages composed by `mkosi`, then staged with systemd's A/B image-update tooling, partition layout and boot assessment. The detailed status and open design questions are in [architecture.md](docs/architecture.md).

The inherited visual assets and their provenance are documented in [branding](branding/README.md).
