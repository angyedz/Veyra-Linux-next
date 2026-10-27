# Native Veyra Live ISO experiment (Anaconda-free)

This is a **new experimental pipeline**. It is independent of the
previous Lorax/Anaconda pipeline and does not change the installed system
or its mkosi A/B layout. It uses Fedora 44 DNF5 RPM composition into an
isolated installroot, builds a dracut Live initramfs, SquashFS root,
and a GRUB rescue ISO with BIOS + UEFI El Torito entries.

CI artifact: `veyra-next-native-live-iso`; GitHub Actions workflow
`Veyra Anaconda-free Live ISO experiment`. Max size 8 GiB.

Calamares is the user-facing installer. Its existing terminal module runs
`/usr/libexec/veyra-installer`. The installed system's package manifest,
repart definitions, and Dracut module are copied into the live source
directory. Only the Live user is granted passwordless permission for the
Calamares binary. Anaconda is neither installed nor invoked by this pipeline.

**Important initial limitation:** the recovery UKI input is optional for
building the first *bootable media* prototype; without
`VEYRA_RECOVERY_UKI=/path/to/veyra-recovery.efi` the installer's
mandatory preflight must reject destructive installation. Do not call a
Live media ISO with no bundled Recovery UKI an installable release.

The GRUB boot options use Dracut's `dmsquash-live` format:
`root=live:CDLABEL=VEYRA_NEXT_LIVE rd.live.overlay.overlayfs=1`;
rootfs is a direct SquashFS (its internal root includes `/proc`).
Verify boot in both firmware modes and check that the KDE Wayland
session / Calamares opens before promoting this prototype.

The source tests validate configuration, not firmware boot. A built ISO
with both El Torito records is **not** itself proof that the kernel,
initrd, writable overlay, desktop, or installer work correctly.
No automatic merge to main and no claim of a usable Live ISO until
CI produces the artifact and QEMU boots both modes.
