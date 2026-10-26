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

The Live desktop launches the Qt 6 Calamares installer. Its Veyra-branded flow opens Calamares' KonsolePart terminal module and runs the existing `/usr/libexec/veyra-installer`, preserving its whole-disk checks and explicit erase confirmation. Anaconda packages are rejected from the built Live system. Lorax still uses Anaconda inside the isolated GitHub Actions builder to compose the ISO; those build-only packages are not part of the downloaded media.

## Open sizing decision

The current partition definitions require at least 55.5 GiB for the installed disk. The installer must report the required capacity before allowing installation. Making the layout fit smaller target disks requires changing the partition sizing contract and is not implied by the 8 GiB ISO limit.
