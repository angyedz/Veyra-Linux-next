# Veyra Next

![Veyra mark](branding/veyra-logo.svg)

Veyra Next is a new Fedora-based desktop OS project. The intended user experience is a normal KDE Plasma Wayland computer for gaming, communication and development. Fedora provides the package ecosystem and drivers; this repository defines Veyra's image and update behavior.

## Current stage

This repository builds its first Fedora 44 + KDE image with `mkosi`. It includes KDE Plasma Setup for first-boot account creation, Plasma Login Manager, Wayland, Flatpak/Discover, Flathub, PipeWire, Git and common native development tools. The disk recipe lays out two EROFS root slots with dm-verity data, plus separate persistent `/etc`, `/var` and `/home` partitions. The initrd mounts the writable state before switching to the installed system.

This is an image-building prototype, not a usable installer or pre-alpha release yet. `systemd-sysupdate` stages the matching verity partition and root into the inactive slot and installs the UKI last with three boot attempts. CI now applies a signed test update with `systemd-sysupdate`, boots the new slot in QEMU/OVMF, checks dm-verity and boot blessing, then verifies fallback after three deliberately failed trial boots. The production update-signing key is not provisioned, so there is no public OS update channel yet. The first-boot account flow still needs a separate VM validation. A graphical installer, Recovery and Developer Mode are not implemented. It has not been tested on real hardware. Steam/Proton, Discord screen sharing and NVIDIA/AMD compatibility are not yet qualified.

The root image is read-only EROFS and has dm-verity integrity data. Update transfers require signed `SHA256SUMS` manifests (`Verify=yes`), but a release signing key is not provisioned yet. The UKIs are unsigned, so the current image boots with Secure Boot disabled and does not authenticate the dm-verity root hash against a hardware trust root.

The image includes `veyra-update` for listing, checking, staging and rebooting into a system update. Staging does not reboot automatically. The manual signed-release workflow embeds the matching public update key in the image, signs the `SHA256SUMS` manifest and publishes the UKI plus matching root/verity assets. It fails closed until `VEYRA_UPDATE_SIGNING_KEY` is provisioned; see [system update signing](docs/system-updates.md). The full disk image is not published as a release asset.

## Optional RPM packages

`vpkg` has two package paths: signed `veyra-*` extension-wrapper RPMs from Veyra's `main` or `testing` channel, and ordinary application RPMs from Fedora/RPM Fusion or a local RPM file. It uses libdnf5's Python API; the runtime image does not install the DNF5 CLI. Ordinary packages are solved and installed in a disposable installroot seeded with the base RPM inventory; their additive `/usr` and `/opt` files become a persistent `vpkg-apps` system extension. Live additive transactions refresh sysext with rollback on failure, while `vpkg stage install` can select an app generation after reboot and `vpkg rollback` restores the previous generation. Kernel, graphics-driver, init, bootloader, base-package replacements, file collisions, privileged setuid/setgid files, and payload outside `/usr` or `/opt` are rejected. Local RPMs must be signed by a key trusted by Fedora or RPM Fusion. Package configuration under `/etc` and third-party signing-key enrollment are not supported yet. The signed transaction and staged rollback QEMU scenarios are authored but still await a clean Fedora 44 run.

Veyra package RPMs are resolved from versioned GitHub Releases, not raw GitHub
channel URLs. Stable selects the highest `rpm-vX.Y.Z` release; testing selects
the highest `rpm-vX.Y.Z-testing` prerelease. Before libdnf5 receives the local
repository path, vpkg checks the release's detached archive SHA256, rejects
unsafe archive entries, and verifies the `repomd.xml` signature against the
image-pinned Veyra RPM key. The Fedora and RPM Fusion repositories remain
official/independent remote sources.

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
- Git, SSH, GCC, Clang, Python, Node.js and CMake are available in the native Fedora userspace. `vforge` provides rootless named and project environments on Fedora 44, permission profiles, snapshots and rollback. Its integrated Veyra lower currently covers `/usr` and the RPM database only; see the implementation limits in [docs/vforge.md](docs/vforge.md).
- System updates preserve personal files and install a complete new OS version beside the active one. A failed trial boot returns to the previous version.
- Optional Veyra add-ons install as signed system extensions, while system packages go through A/B image updates.

Those are product goals, not claims that this image already provides them.

`vforge` supports project `.vforge.toml`, named and project storage, explicit
mount permissions, isolation profiles, snapshots/rollback and Veyra runtime
compatibility checks. The full filtered `/etc` lower, whole-root Veyra
integration, quota enforcement and crash-atomic CoW snapshots remain
unimplemented. See [the vforge implementation and limits](docs/vforge.md).

## Architectural direction

Kinoite is a reference for the KDE Atomic user experience, not the implementation to rebrand. The proposed Veyra path is Fedora packages composed by `mkosi`, then staged with systemd's A/B image-update tooling, partition layout and boot assessment. The detailed status and open design questions are in [architecture.md](docs/architecture.md).

The inherited visual assets and their provenance are documented in [branding](branding/README.md).
