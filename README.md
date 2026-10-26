# Veyra Next

![Veyra mark](branding/veyra-logo.svg)

Veyra Next is a new Fedora-based desktop OS project. The intended user experience is a normal KDE Plasma Wayland computer for gaming, communication and development. Fedora provides the package ecosystem and drivers; this repository defines Veyra's image and update behavior.

## Current stage

This repository is building its first bootable Fedora 44 + KDE image with `mkosi`. It includes KDE Plasma Setup for first-boot account creation, Plasma Login Manager, Wayland, Flatpak/Discover, Flathub, PipeWire, Git and common native development tools.

This is an image-building prototype, not a usable installer or pre-alpha release yet. The first-boot account flow is configured but still needs VM validation. A/B updates, verified boot, automatic rollback, Recovery and Developer Mode are not implemented. It has not been tested on real hardware. Steam/Proton, Discord screen sharing and NVIDIA/AMD compatibility are not yet qualified.

The first `v-dri` slice provides read-only DRM/driver diagnostics, reports out-of-tree module taint, and checks that the Fedora kernel enforces module signatures. It does not install drivers. See [the v-dri status](docs/v-dri.md).

The test image uses unsigned UKIs and therefore boots with Secure Boot disabled. Secure Boot enrollment and signed boot artifacts remain future work.

## Build

Requirements: Linux with systemd 254+, `uv`, user namespaces, and the dependencies reported by `scripts/mkosi.sh dependencies`. The builder itself is pinned to an upstream mkosi revision.

```sh
python3 scripts/smoke.py
scripts/mkosi.sh --force build
```

The disk image and UKI artifacts are written to `mkosi.output/`. Boot the image in a disposable VM with:

```sh
scripts/mkosi.sh vm --console=gui
```

The image is configured to offer KDE Plasma Setup on first boot so the user can create an ordinary account before reaching Plasma. Verify that flow in the disposable VM before treating the image as usable.

## How the finished desktop should feel

- The user signs into KDE Plasma and installs desktop apps from Discover.
- Steam and Discord are ordinary desktop apps, not separate operating systems. Steam runs games with Proton; Discord receives Wayland screen sharing through KDE's portal and PipeWire.
- Git, SSH, GCC, Clang, Python, Node.js and CMake run directly in the native Fedora userspace. Project files live in the user's home directory. Toolbx/container use stays optional.
- System updates preserve personal files and install a complete new OS version beside the active one. A failed trial boot returns to the previous version.

Those are product goals, not claims that this image already provides them.

## Architectural direction

Kinoite is a reference for the KDE Atomic user experience, not the implementation to rebrand. The proposed Veyra path is Fedora packages composed by `mkosi`, then staged with systemd's A/B image-update tooling, partition layout and boot assessment. The detailed status and open design questions are in [architecture.md](docs/architecture.md).

The inherited visual assets and their provenance are documented in [branding](branding/README.md).
