# Veyra Next architecture — working proposal

**Status:** source now defines an EROFS + dm-verity A/B disk layout, early persistent-state mounts, `systemd-sysupdate` transfer definitions and a three-try boot-assessment policy. CI must build and inspect the image, and a VM must prove update and rollback. There is no signed release channel, installer, Recovery or Developer Mode yet. This is not a release claim.

## Product shape

One installed Linux system: Fedora packages, glibc, systemd, KDE Plasma on Wayland, one login session and ordinary home directories. No second everyday root, nested init system, glenv or mandatory development container.

Kinoite informs the desktop experience: Plasma, Discover, Flatpak and an atomic-system update flow. Veyra does not use the Kinoite OSTree/rpm-ostree delivery model as its intended system-update mechanism.

## Selected prototype foundation

The current implementation uses Fedora 44, glibc and systemd. This replaces the earlier ADR's OpenRC preference for the new project: systemd is already the native service manager on Fedora and provides the boot, partition and image-update tools being evaluated. `mkosi` composes the image, `systemd-repart` lays out its disk, and `systemd-sysupdate` stages updates. Their end-to-end behavior still needs to be demonstrated as Veyra behavior rather than inherited from Kinoite.

The current UKIs are unsigned and the VM uses ordinary UEFI. Secure Boot is deliberately not claimed or enabled until key ownership, signing and enrollment are designed.

## Build and release direction

1. `mkosi` builds the Fedora 44 package set and two fixed-size root slots. Slot A is EROFS with a matching dm-verity hash partition; slot B starts empty. The current roots are each 8 GiB, and each verity slot is 512 MiB.
2. The image also has writable ext4 partitions for `/etc` and `/var`, mounted from the initrd before switch-root, and a separate persistent `/home` partition. Cross-version compatibility and migration for persistent state still need a policy.
3. `systemd-sysupdate` transfers the matching verity and root images to the unused partitions. Release filenames carry the two halves of the dm-verity root hash; `PartitionUUID=@u` preserves those UUIDs when writing the new slots. The UKI is transferred last and receives three boot attempts; its presence is the entry point for the new version. The current version is protected while an update is staged.
4. Update definitions require GPG verification of `SHA256SUMS` (`Verify=yes`). The build compresses split root artifacts and versions the UKI, but release publication and the trusted Veyra update key are not configured yet. The updater therefore has no usable signed channel today.
5. `systemd-boot` boot counting and an enabled systemd boot health check are configured to bless a trial only after the boot reaches the completion target without failed units. The A→B transition and automatic return after three failed attempts remain to be demonstrated in a VM.

This reuses systemd's image, partition and boot tools instead of writing a replacement package manager or custom cryptographic format. The A/B layout is two versions of one OS, never two concurrently running user environments.

## Add-on package boundary

Veyra's optional RPM repository is separate from Fedora's system repositories. Its two package channels are directories named `main` and `testing`; neither channel is a Git branch. Fedora continues to provide the system package set used when composing each A/B image. The Veyra repository is reserved for additional Veyra packages.

The first `vpkg` implementation delegates dependency solving and RPM database transactions to DNF5, but narrows the allowed transaction to a selected Veyra repository and a separate installroot under writable `/var`. This keeps the add-on RPM database, transaction history, package cache, and extension RPM payloads separate from Fedora's RPM database in immutable `/usr/lib/sysimage`. It pre-downloads and inspects the resolved RPM set, rejects packages outside the `veyra-*` namespace, rejects RPM scripts, triggers, obsoletes and conflicts, and allows only one `.raw` system-extension payload under the installroot's `/var/lib/extensions`. `vpkg` exposes that image through a symlink in `/etc/extensions` and refreshes `systemd-sysext`; if activation fails, it uses the just-recorded DNF history transaction to undo the package operation and refreshes extensions again. RPM metadata and package signatures are required by both repository definitions.

System extensions provide additive `/usr` and `/opt` content and can be activated without rebooting. Their extension-release metadata must match the Fedora release used by the base image. The first extension source, `veyra-shell-tools`, resolves Fedora dependencies in a root seeded with the Veyra base package set, rejects changes to any already-installed base package, and copies only newly introduced files into the extension image. A package that needs to replace a base file is rejected and must instead be delivered in a complete A/B system update. This gives Veyra two explicit transaction paths: add-on applications/extensions use DNF5 plus systemd-sysext; changes to the system image use A/B sysupdate.

The package build scripts and repository directory manifests are in `packages/` and `scripts/`. CI is intended to build and sign a disposable test repository to validate the format. No production RPM key is committed or provisioned, no Pages site is enabled, and no package channel is published yet. A generated CI key must never become the key trusted by Veyra clients.

## Integrity layers

- Fedora RPM signatures authenticate source packages during image composition.
- A signed Veyra release manifest is required by the updater before accepting downloaded update files, but no release key is provisioned yet.
- dm-verity verifies the immutable system image while it is read.
- The current UKIs are unsigned, so the embedded root hash is not yet anchored by Secure Boot.
- SELinux remains enforcing; privileged system changes are explicit.

These checks protect different stages. A signed manifest alone does not authenticate the machine's boot path. Secure Boot signing/enrollment, recovery from lost signing keys and key rotation are not implemented yet.

## Desktop and applications

The base image carries the coherent kernel, graphics stack, KDE session, display manager, audio and portal services. Discover and Flatpak provide desktop applications independently of the base OS. The user can install Steam and Discord through the app catalog without launching another distribution. Proton and Wayland sharing still require real application and hardware tests.

The default image includes common native development tools. Additional per-project environments may use Toolbx or other containers as optional conveniences; everyday coding must work directly in the host account.

## Persistent state — initial layout configured

The image recipe keeps `/etc`, `/var` and `/home` on writable partitions outside both root slots. The custom initrd mounts `/etc` and `/var` before switch-root; systemd discovers the home partition by its GPT type. This prevents ordinary root replacement from erasing those trees. It does not yet solve schema changes in `/var` or `/etc`: a rollback can run an older OS against state written by a newer one, so update releases need compatibility checks and migrations before this is safe for daily use.

## Recovery and developer use — not implemented

Recovery should boot independently of the installed system, offer logs and a rescue shell, and allow selecting a known-good deployment. Developer Mode should build/test a separate Veyra image and boot it through the same explicit trial process; normal app use must not weaken the boot trust policy.

## Acceptance boundary

Pre-alpha should not be called usable until there is a user-creation/install path, KDE Wayland login, actual update A → B → failed-boot rollback, persistent home checks, Recovery boot, and application testing. Steam/Proton and Discord screen sharing must be exercised on real NVIDIA and AMD hardware before compatibility is claimed.
