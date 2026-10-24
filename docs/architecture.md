# Veyra Next architecture — working proposal

**Status:** first Fedora KDE disk-image recipe is being built. A/B update, verified boot, user-state migration, installer, Recovery and Developer Mode are specified only as goals. This is not a release claim.

## Product shape

One installed Linux system: Fedora packages, glibc, systemd, KDE Plasma on Wayland, one login session and ordinary home directories. No second everyday root, nested init system, glenv or mandatory development container.

Kinoite informs the desktop experience: Plasma, Discover, Flatpak and an atomic-system update flow. Veyra does not use the Kinoite OSTree/rpm-ostree delivery model as its intended system-update mechanism.

## Selected prototype foundation

The current implementation uses Fedora 44, glibc and systemd. This replaces the earlier ADR's OpenRC preference for the new project: systemd is already the native service manager on Fedora and provides the boot, partition and image-update tools being evaluated. `mkosi` composes the image; `systemd-repart` defines its disk; `systemd-sysupdate` is the leading A/B update candidate. None of the update, rollback or Recovery pieces are implemented yet. They will be proved as Veyra behavior rather than inherited from Kinoite.

The current UKIs are unsigned and the VM uses ordinary UEFI. Secure Boot is deliberately not claimed or enabled until key ownership, signing and enrollment are designed.

## Build and release direction

1. `mkosi` consumes a versioned Fedora package manifest and Veyra configuration.
2. The build produces a bootable disk image and UKI. A future image layout will include two system slots, matching integrity data and persistent user state.
3. A release pipeline will produce versioned artifacts plus a cryptographically signed manifest. The signing policy and key rotation need to be designed before publication.
4. On the machine, `systemd-sysupdate` will stage the complete new resource set into the inactive slot. `systemd-repart` will define and grow the partition layout.
5. `systemd-boot` trial-boot counting and a Veyra health check will mark the image good only after the desktop reaches the chosen healthy state. A failed trial returns to the previous slot.

This reuses systemd's image, partition and boot tools instead of writing a replacement package manager or custom cryptographic format. The A/B layout is two versions of one OS, never two concurrently running user environments.

## Integrity layers

- Fedora RPM signatures authenticate source packages during image composition.
- A signed Veyra release manifest authenticates the files distributed by the update channel.
- A UKI signed for Secure Boot authenticates the boot entry and its embedded root digest.
- dm-verity verifies the immutable system image while it is read.
- SELinux remains enforcing; privileged system changes are explicit.

These checks protect different stages. A signed manifest alone does not authenticate the machine's boot path. Key enrollment, recovery from lost signing keys and key rotation are not implemented yet.

## Desktop and applications

The base image carries the coherent kernel, graphics stack, KDE session, display manager, audio and portal services. Discover and Flatpak provide desktop applications independently of the base OS. The user can install Steam and Discord through the app catalog without launching another distribution. Proton and Wayland sharing still require real application and hardware tests.

The default image includes common native development tools. Additional per-project environments may use Toolbx or other containers as optional conveniences; everyday coding must work directly in the host account.

## Persistent state — unresolved

`/home` must survive OS updates and rollback. We still need to define how `/etc` machine configuration and `/var` service state migrate between versions, and what happens if a rollback encounters data written by the newer release. The policy must avoid silently reverting user files or booting an older image against incompatible state.

## Recovery and developer use — not implemented

Recovery should boot independently of the installed system, offer logs and a rescue shell, and allow selecting a known-good deployment. Developer Mode should build/test a separate Veyra image and boot it through the same explicit trial process; normal app use must not weaken the boot trust policy.

## Acceptance boundary

Pre-alpha should not be called usable until there is a user-creation/install path, KDE Wayland login, actual update A → B → failed-boot rollback, persistent home checks, Recovery boot, and application testing. Steam/Proton and Discord screen sharing must be exercised on real NVIDIA and AMD hardware before compatibility is claimed.
