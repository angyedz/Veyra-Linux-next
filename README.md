# Veyra Next

![Veyra mark](branding/veyra-logo.svg)

Veyra Next is a new Fedora-based desktop OS project. The intended user experience is a normal KDE Plasma Wayland computer for gaming, communication and development. Fedora provides the package ecosystem and drivers; this repository defines Veyra's image and update behavior.

## Current stage

This repository builds its first Fedora 44 + KDE image with `mkosi`. It includes KDE Plasma Setup for first-boot account creation, Plasma Login Manager, Wayland, Flatpak/Discover, Flathub, PipeWire, Git and common native development tools. The disk recipe lays out two EROFS root slots with dm-verity data, plus separate persistent `/etc`, `/var` and `/home` partitions. The initrd mounts the writable state before switching to the installed system.

This is an image-building prototype, not a usable installer or pre-alpha release yet. `systemd-sysupdate` stages the matching verity partition and root into the inactive slot and installs the UKI last with three boot attempts. CI now applies a signed test update with `systemd-sysupdate`, boots the new slot in QEMU/OVMF, checks dm-verity and boot blessing, then verifies fallback after three deliberately failed trial boots. The production update-signing key is not provisioned, so there is no public OS update channel yet. The first-boot account flow still needs a separate VM validation. A graphical installer, Recovery and Developer Mode are not implemented. It has not been tested on real hardware. Steam/Proton, Discord screen sharing and NVIDIA/AMD compatibility are not yet qualified.

The root image is read-only EROFS and has dm-verity integrity data. Update transfers require signed `SHA256SUMS` manifests (`Verify=yes`), but a release signing key is not provisioned yet. The UKIs are unsigned, so the current image boots with Secure Boot disabled and does not authenticate the dm-verity root hash against a hardware trust root.

The image includes `veyra-update` for listing, checking, staging and rebooting into a system update. Staging does not reboot automatically. The manual signed-release workflow embeds the matching public update key in the image, signs the `SHA256SUMS` manifest and publishes the UKI plus matching root/verity assets. It fails closed until `VEYRA_UPDATE_SIGNING_KEY` is provisioned; see [system update signing](docs/system-updates.md). The full disk image is not published as a release asset.

## Optional RPM packages

`vpkg` has two package paths: signed `veyra-*` extension-wrapper RPMs from Veyra's `main` or `testing` channel, and ordinary application RPMs from Fedora/RPM Fusion or a local RPM file. Ordinary packages are solved and installed in a disposable DNF5 installroot seeded with the base RPM inventory; their additive `/usr` and `/opt` files become one persistent `vpkg-apps` system extension. Activation is a live sysext refresh with rollback on failure. Kernel, graphics-driver, init, bootloader, base-package replacements, file collisions, privileged setuid/setgid files, and payload outside `/usr` or `/opt` are rejected. Local RPMs must be signed by a key trusted by Fedora or RPM Fusion. Package configuration under `/etc` and third-party signing-key enrollment are not supported yet. CI exercises Fedora repository resolution, local signed RPM installation and removal, and kernel-package rejection inside the VM.

The first optional Veyra package is `veyra-shell-tools` (bash completion, fzf, jq and ripgrep). The packaging pipeline builds it as a version-matched system extension and wraps it in an RPM. Veyra package channels require both RPM and repository-metadata signatures. There is no production key or published repository yet, so Veyra package search and installation are unavailable until the signing key and repository hosting are provisioned. Fedora and RPM Fusion package discovery use their own configured sources. See [package repository details](packages/README.md).

## Build

Requirements: Linux with systemd 254+, `uv`, user namespaces, and the dependencies reported by `scripts/mkosi.sh dependencies`. Allow a 64 GiB virtual disk for the current fixed-size A/B prototype. The builder itself is pinned to an upstream mkosi revision.

```sh
python3 scripts/smoke.py
scripts/mkosi.sh --force build
```

The disk image, versioned UKI, and compressed root/verity update artifacts are written to `mkosi.output/`. CI builds and boots a real signed A/B update in a disposable QEMU VM, including the three-try rollback. To inspect the first-boot desktop setup manually, boot the base image in a separate disposable VM:

```sh
scripts/mkosi.sh vm --console=gui
```

The image is configured to offer KDE Plasma Setup on first boot so the user can create an ordinary account before reaching Plasma. Verify that flow and both persistent state mounts. The CI update-signing key is temporary and generated for each run; the source still has no production signed release or trusted update key, so the configured updater cannot install a public release yet.

## How the finished desktop should feel

- The user signs into KDE Plasma and installs desktop apps from Discover.
- Steam and Discord are ordinary desktop apps, not separate operating systems. Steam runs games with Proton; Discord receives Wayland screen sharing through KDE's portal and PipeWire.
- Git, SSH, GCC, Clang, Python, Node.js and CMake are available in the native Fedora userspace. `vforge` is planned for both project-described environments and persistent, named development environments; it is optional, and projects can still be built directly on the host.
- System updates preserve personal files and install a complete new OS version beside the active one. A failed trial boot returns to the previous version.
- Optional Veyra add-ons install as signed system extensions, while system packages go through A/B image updates.

Those are product goals, not claims that this image already provides them.

The `vforge` environment manager is a design goal and is not implemented yet. Its
planned interface and isolation boundary are described in [the vforge design](docs/vforge.md).

## Architectural direction

Kinoite is a reference for the KDE Atomic user experience, not the implementation to rebrand. The proposed Veyra path is Fedora packages composed by `mkosi`, then staged with systemd's A/B image-update tooling, partition layout and boot assessment. The detailed status and open design questions are in [architecture.md](docs/architecture.md).

The inherited visual assets and their provenance are documented in [branding](branding/README.md).
