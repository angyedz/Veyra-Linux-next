# Veyra Next architecture — working proposal

**Status:** source defines an EROFS + dm-verity A/B disk layout, early persistent-state mounts, `systemd-sysupdate` transfers and a three-try boot-assessment policy. CI prepares signed test artifacts on the host, then runs `veyra-update` and OTA actions inside a disposable QEMU/OVMF guest. The guest verifies preflight conflict handling, mutation locking, old-UKI selection, blessing, persistent `/etc`, `/var` and `/home`, and fallback after three deliberately failed trial boots. The VM also exercises signed `vpkg` install/upgrade/remove with the real `systemd-sysext`, then checks that a deliberately invalid extension image causes its RPM transaction to roll back. A separate Fedora 44 Recovery UKI is now built into the ESP; its independent boot and one-shot rollback path still need QEMU validation. No production update or package channel is signed or published yet; installer and Developer Mode are not implemented.

## Product shape

One installed Linux system: Fedora packages, glibc, systemd, KDE Plasma on Wayland, one login session and ordinary home directories. No second everyday root, nested init system, glenv or mandatory development container.

Kinoite informs the desktop experience: Plasma, Discover, Flatpak and an atomic-system update flow. Veyra does not use the Kinoite OSTree/rpm-ostree delivery model as its intended system-update mechanism.

## Selected prototype foundation

The current implementation uses Fedora 44, glibc and systemd. This replaces the earlier ADR's OpenRC preference for the new project: systemd is already the native service manager on Fedora and provides the boot, partition and image-update tools being evaluated. `mkosi` composes the image, `systemd-repart` lays out its disk, and `systemd-sysupdate` stages updates. CI now exercises their end-to-end A/B behavior on a Veyra image rather than treating configuration alone as proof.

The CI boot exercise uses ordinary UEFI with Secure Boot disabled. It proves the update and boot-count behavior in a virtual machine; it does not qualify physical hardware or authenticate the EFI boot path. Secure Boot is deliberately not claimed or enabled until key ownership, signing and enrollment are designed.

## Build and release direction

1. `mkosi` builds the Fedora 44 package set and two fixed-size root slots as a plain GPT disk image. It omits an El Torito ISO9660 descriptor so `systemd-sysupdate` can dissect the root and verity partitions consistently. Slot A is EROFS with a matching dm-verity hash partition; slot B starts empty. The current roots are each 8 GiB, and each verity slot is 512 MiB.
2. The image also has writable ext4 partitions for `/etc` and `/var`, mounted from the initrd before switch-root, and a separate persistent `/home` partition. Cross-version compatibility and migration for persistent state still need a policy.
3. `systemd-sysupdate` transfers the matching verity and root images to the unused partitions. Release filenames carry the two halves of the dm-verity root hash; `PartitionUUID=@u` preserves those UUIDs when writing the new slots. The target root and package requests are preflighted before its generation is bound to the exact `(roothash, IMAGE_VERSION)` identity. The UKI is transferred last and receives three boot attempts; its presence is the entry point for the new version. The current version is protected while an update is staged.
4. Update definitions require GPG verification of `SHA256SUMS` (`Verify=yes`). The manual release workflow builds with the public half of a dedicated Veyra update key, signs the root/verity/UKI manifest and publishes those assets. Until its private key is provisioned and the workflow is run from `main`, there is no usable signed release today.
5. `systemd-boot` boot counting and an enabled systemd boot health check bless a trial only after the boot reaches the completion target without failed units. CI applies a signed B-slot update, confirms it reaches `graphical.target`, `boot-complete.target` and the successful bless service, then boots a deliberately failing higher-version entry three times and confirms that the next boot returns to the previously blessed slot.

This reuses systemd's image, partition and boot tools instead of writing a replacement package manager or custom cryptographic format. The A/B layout is two versions of one OS, never two concurrently running user environments.

## Add-on package boundary

Veyra's optional RPM repository is separate from Fedora's system repositories. Its two package channels are directories named `main` and `testing`; neither channel is a Git branch. Fedora continues to provide the system package set used when composing each A/B image. The Veyra repository is reserved for additional Veyra packages.

`vpkg` uses separate DNF5 installroots for Veyra wrappers and Fedora/RPM Fusion application packages. For ordinary app requests, it copies the base RPM database into a disposable installroot, solves and applies the requested transaction there, then rejects the result if any base RPM changed. The staged app payload becomes one persistent `vpkg-apps` directory extension; the host RPM database remains unchanged. Fedora and RPM Fusion repositories remain separate sources, with RPM Fusion nonfree packages available for installation. Local RPMs are accepted when their signatures match a Fedora or RPM Fusion key trusted in the image. The Veyra repository key remains embedded in the immutable image, and its metadata signatures remain required.

The app extension is limited to additive `/usr` and `/opt` content. Its RPM payload cannot collide with a file owned by the base, contain setuid/setgid files, Linux capabilities or special files, or touch `/etc`, kernel, graphics-driver, init, boot, RPM-database, systemd-core, or firmware paths. Shared directories such as `/usr/bin` are permitted; existing files cannot be replaced. Third-party local-key enrollment and `/etc` configuration payloads are not implemented. Extension-release metadata matches Fedora 44 and the Veyra system-extension ABI level. The boot health check confirms installed extensions activate and compares app-layer files with the current base RPM inventory before blessing an A/B candidate. If a newer base image introduces a collision or the extension is incompatible, that candidate is not blessed and systemd-boot returns to the previous slot. Changes to the base system remain complete A/B image updates.

Durable user requests live in `/var/lib/veyra/vpkg/intents.json`; resolved generation manifests keep the exact requests and installed RPM NEVRAs separate. A candidate root can be resolved with `vpkg prepare-for-base`, which creates an inactive generation under `/var/lib/veyra/vpkg/app-layers` and returns its absolute path in JSON. Deployment bindings are stored by the pair of full dm-verity `roothash` and UKI `IMAGE_VERSION`; early `vpkg sync` selects the exact matching generation before systemd-sysext. `bind-generation` also records the pending target pair and blocks all package mutations until that exact root and UKI are blessed or OTA explicitly cancels the target trial entry. A mismatched boot alone does not release the lock because the target may remain selectable.

The package build scripts and repository directory manifests are in `packages/` and `scripts/`. CI builds and signs a disposable Veyra test repository, verifies both channels, and exercises mirroring. The image CI additionally resolves a Fedora package from an offline fixture repository, installs a local signed RPM, removes app-layer packages, and verifies that a kernel package is rejected without changing the base RPM inventory. The manual publisher can create `testing` snapshots and promote selected signed packages to `main`, but the production RPM key is not provisioned, Pages is not enabled and no Veyra package channel is published. CI keys are test-only.

## Integrity layers

- Fedora RPM signatures authenticate source packages during image composition.
- A signed Veyra release manifest is required by the updater before accepting downloaded update files, but no release key is provisioned yet.
- dm-verity verifies the immutable system image while it is read.
- The current UKIs are unsigned, so the embedded root hash is not yet anchored by Secure Boot.
- SELinux remains enforcing; privileged system changes are explicit.

These checks protect different stages. A signed manifest alone does not authenticate the machine's boot path. Secure Boot signing/enrollment, recovery from lost signing keys and key rotation are not implemented yet.

## Desktop and applications

The base image carries the coherent kernel, graphics stack, KDE session, display manager, audio and portal services. Discover and Flatpak provide desktop applications independently of the base OS. The user can install Steam and Discord through the app catalog without launching another distribution. Proton and Wayland sharing still require real application and hardware tests.

The default image includes common native development tools. An early native
`vforge` implementation supports project-local environments described by a
configuration file and persistent named environments. Each environment uses
the active read-only Veyra EROFS root as its OverlayFS lower and keeps its own
writable upper. Everyday coding remains available directly in the host
account. Vforge has not yet passed a Veyra guest runtime test; package
generations, snapshots, network approval, and OTA compatibility are pending.
See [`vforge` implementation and limits](vforge.md).

## Persistent state — initial layout configured

The image recipe keeps `/etc`, `/var` and `/home` on writable partitions outside both root slots. The custom initrd mounts `/etc` and `/var` before switch-root; systemd discovers the home partition by its GPT type. This prevents ordinary root replacement from erasing those trees. It does not yet solve schema changes in `/var` or `/etc`: a rollback can run an older OS against state written by a newer one, so update releases need compatibility checks and migrations before this is safe for daily use.

## Kernel scope

The A/B image carries Veyra's selected Fedora kernel. Installing a custom kernel is outside the current product scope; `vpkg` does not provide this operation, and A/B updates replace the kernel with the version in the new image. A standalone UKI copied to EFI is unmanaged and may no longer match the system's modules.

## Recovery — source implementation, QEMU validation pending

A separate minimal Fedora 44 UKI is configured to be built into the ESP. It includes its own
kernel and userspace in the UKI initrd and does not start the installed Veyra
root or KDE. Its initial console offers read-only boot-entry and journal
inspection, a rescue shell, and one-time boot of a previously successful Veyra
deployment. Only uncounted Veyra UKIs other than the current entry are eligible;
the selected identifier is passed as one argument to `bootctl set-oneshot`
after an explicit confirmation. The recovery path does not edit system
partitions. It is unsigned in this prototype and is not Secure-Boot protected.
Its independent QEMU boot, journal access, and previous-deployment selection
still need runtime validation. Vpkg-generation and filesystem repair are not
provided yet.

Developer Mode is
an opt-in mutable mode for the installed host, separate from Vforge's isolated
development environments. On Fedora 44, systemd 259's `systemd-sysext`
`Mutable=yes` support is a practical starting point: writes to merged `/usr`
and `/opt` are routed to persistent state under
`/var/lib/extensions.mutable/`, while the original EROFS + dm-verity slot stays
unchanged. See the [Developer Mode design](developer-mode.md).

While this mode is active, Veyra's system-update commands must refuse to stage
or boot A/B updates. Enabling it must also refuse a pending trial update. The
desktop and command line must warn that mutable files are outside dm-verity
and that package, driver, boot or service changes can break the system. A
Recovery path must be able to boot without the mutable layer and either
preserve it for repair or reset it before normal A/B updates resume. This
feature is not implemented yet and needs Fedora VM tests for enable/disable,
RPM changes, Recovery without the mutable layer, and update blocking.

## Acceptance boundary

Pre-alpha should not be called usable until there is a user-creation/install path, KDE Wayland login, the CI A → B and failed-boot rollback job passing, persistent home checks across an update, Recovery boot, and application testing. Steam/Proton and Discord screen sharing must be exercised on real NVIDIA and AMD hardware before compatibility is claimed.
