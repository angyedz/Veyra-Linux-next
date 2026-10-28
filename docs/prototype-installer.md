# Veyra Next Prototype Live Installer

## Capability map and build order

| Capability | Responsibility | Depends on |
|---|---|---|
| `live-session` | Boot a Veyra-branded KDE session from one ISO and launch the installer | — |
| `target-compose` | Install Fedora 44 packages and compose Veyra's A/B, verity, UEFI, and BIOS layout on the selected disk | `live-session` |
| `iso-ci` | Build one ISO in GitHub Actions, enforce the size limit, and install/boot it in QEMU | `live-session`, `target-compose` |

Build order: `live-session` → `target-compose` → `iso-ci`.

## Objective

Produce one bootable Live ISO, no larger than 8 GiB, that lets someone try the Veyra-branded KDE session and install a complete Veyra Next system. The ISO contains the live environment and installer, not a second prebuilt system disk. During installation, the installer obtains the required Fedora 44 packages from Fedora repositories, assembles the selected target's Veyra layout, and leaves it ready to boot.

## Acceptance criteria

- The ISO boots in UEFI and Legacy BIOS modes.
- The Live session can reach the desktop and start the installer.
- The installer lists whole disks with model and capacity, excludes the running live medium and mounted targets, and makes no changes before explicit confirmation.
- The installer uses Fedora 44 packages and installs Veyra's immutable A/B roots, dm-verity, recovery, UEFI systemd-boot, and Legacy BIOS GRUB assets.
- Installed Veyra retains `libdnf5` for `vpkg`; the system's DNF command-line package is not installed in the target OS.
- GitHub Actions enforces an ISO size ceiling of 8 GiB and boots the ISO, installs to a disposable QEMU disk, then boots the installed system.
- No full image build runs on the user's computer.

## ISO build troubleshooting

Lorax's Fedora 44 `live/x86.tmpl` generates `EFI/BOOT` and its EFI
boot image only when the installed live root contains
`/boot/efi/EFI/*/gcdx64.efi`. Fedora supplies this file in
`grub2-efi-x64-cdboot`. The earlier kickstart installed BIOS GRUB
but omitted the EFI removable-media package, so CI run 40372875288
failed at xorriso after spending time building SquashFS.

The live package manifest now includes `grub2-efi-x64-cdboot` and
`dracut-config-generic`, and the live post-install script verifies
the EFI CD bootloader file before Lorax begins ISO assembly. This is
an early source/builder check, **not** proof that UEFI, BIOS, Plasma,
Calamares, or installed-system A/B boot succeeds.

## Implementation and verification

The Live environment and install-media build run only on GitHub Actions. The install flow must use the existing Veyra package manifest and boot/update definitions rather than a separately maintained package list. CI must verify both the ISO and the resulting installed system in disposable QEMU guests.

**Verification gap (28 October 2026):** The native experimental Live ISO was independently observed at 300 seconds in BIOS and UEFI QEMU with an actual KDE Plasma desktop, while `vpkg` Live guest tests exercised **offline policy only**. The `live-installer-iso` job in `.github/workflows/ci.yml` currently builds and publishes an installer ISO; it has **no** destructive disposable-disk install-and-reboot stage. This is an acceptance criterion, not a completed capability. Before declaring Pre-alpha, add a separate disposable 64 GiB QEMU install test with a verified Recovery UKI and a real first boot, keeping destructive operations away from all real user disks.

The Fedora image BIOS smoke job in run `40636497267` demonstrated why guest errors matter even when CI is green: `VEYRA_BIOS_BOOT_CI_PASS` was emitted while `vdri-qemu-smoke.service` failed (status 2). A first harness fix attached `virtio-vga` and required `VDRI_QEMU_PASS` in addition to the BIOS marker. That stricter gate **correctly failed** on new run `40638902157`: the kernel log now shows `virtio_gpu` bound, but the legacy BIOS GRUB command line is missing `module.sig_enforce=1`, required by the `v-dri` security policy and already used by UEFI UKIs. The follow-up code sets `module.sig_enforce=1` on **both stable and trial** GRUB entries and adds a regression test. A new real QEMU run after this second fix is still required.

The Live desktop launches the Qt 6 Calamares installer. Its Veyra-branded flow opens Calamares' KonsolePart terminal module and runs the existing `/usr/libexec/veyra-installer`, preserving its whole-disk checks and explicit erase confirmation. The Calamares Qt frontend runs as `liveuser`, not root; only the preflighted installer backend uses restricted `sudo`. The welcome module must **not** require a root frontend (that previously blocked the intended Wayland flow); its network requirement remains because the backend downloads Fedora packages. Opening the Calamares window and completing an installation still require real GUI/QEMU verification. Anaconda packages are rejected from the built Live system. Lorax still uses Anaconda inside the isolated GitHub Actions builder to compose the ISO; those build-only packages are not part of the downloaded media.

## Safe installation preflight

A Live user can validate the bundled installer assets and target sizing **without
touching any disk**:

```sh
/usr/libexec/veyra-installer --preflight
/usr/libexec/veyra-installer --list-disks
```

On a connected disposable VM with an explicit target, use
`/usr/libexec/veyra-installer --preflight --target /dev/vdb`. Preflight
checks the local Fedora package manifest, GPT partition definitions, built-in
GRUB helper, Dracut module and structural Recovery UKI metadata: PE/COFF
sections, Fedora 44 identity, `IMAGE_ID=veyra-recovery`, version, and independent
`rdinit=/init`. A nonempty placeholder or a normal system UKI is rejected
**before disk erasure**. It reports the minimum target capacity. It does
**not** partition, format, download or install. These checks do not validate
a cryptographic signature or prove that Recovery boots: the current prototype
Recovery UKI is unsigned, and standalone QEMU boot acceptance is still required.

Actual destructive installation still requires selecting an eligible whole
disk and typing the exact `ERASE /dev/... ` confirmation string. After that
confirmation, disk eligibility and identity (path, size, model, major/minor,
serial and WWN when reported) are rechecked before the first repartition.
Read-only, mounted, active-swap and Live-media devices remain excluded.

**Important:** these checks are defense-in-depth, not a guarantee against
hostile privileged processes, faulty firmware, or a device disappearing after
the final check. Perform the first destructive tests only on a disposable
QEMU disk, never against a development workstation's real block devices.

## Installer boot-asset lifetime (fix pending end-to-end verification)

The installer initially composes Fedora into a temporary Btrfs home partition,
then runs `systemd-repart` to copy it into the immutable A/B roots. The
temporary Btrfs filesystem is unmounted and reformatted as the persistent
ext4 `/home` **before** writing the final EFI UKI and BIOS kernel/initrd.
The original implementation retained source paths under that temporary root,
causing `ukify` and BIOS asset copies to reference files that had disappeared
after reformatting. The installer now stages a private, disposable copy of
`vmlinuz`, `initramfs.img`, and `os-release` on the Live host first, uses
only those staged copies for UKI/GRUB, and removes them in `finally`.

This fixes a source-level lifetime bug but does **not** prove successful real
installation, UEFI/BIOS first boot, user creation, or Recovery boot. Verify
with a disposable disk in a full end-to-end QEMU acceptance run.

## Open sizing decision

The current partition definitions require at least 55.5 GiB for the installed disk. The installer must report the required capacity before allowing installation. Making the layout fit smaller target disks requires changing the partition sizing contract and is not implied by the 8 GiB ISO limit.
