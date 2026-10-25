# Veyra Recovery

## Current implementation

The image recipe is configured to include a separately built Fedora 44 recovery UKI at
`/EFI/Linux/veyra-recovery.efi`. It embeds its own kernel and minimal userspace
in the UKI initrd and does not mount or execute the installed Veyra root. The
recovery UKI is listed directly by systemd-boot beside the normal Veyra UKIs.

The first recovery slice offers:

- read-only enumeration of EFI boot entries;
- selection of an already successful Veyra entry for one boot;
- read-only access to persistent `/var/log/journal` history;
- a local root shell for manual repair;
- power off.

The rollback action filters out the current entry, non-Veyra entries, and any
entry with a boot-attempt counter. It requires an explicit `BOOT` confirmation,
then asks systemd-boot to select that exact previous entry once. It does not
delete a deployment or alter root, `/etc`, `/var`, or `/home`. If a pending OTA
trial is canceled by booting its prior successful UKI, the normal Veyra boot
settlement service remains responsible for releasing the associated vpkg lock.

The recovery UKI is unsigned in this Fedora 44 prototype. Secure Boot is not
claimed; production use requires signing and validation in the Veyra boot trust
chain. Recovery currently reads the current persistent journal and returns to
a previous successful system deployment. A dedicated vpkg generation repair
operation, filesystem repair workflow, and automated corruption recovery are
not yet implemented.

## Build and test

The `recovery` mkosi subimage is configured to build independently and be copied to the main
image's ESP during finalization. The image build has not yet confirmed this path. Unit tests validate boot-entry parsing,
trial filtering, safe one-shot selection, and strict recovery-UKI artifact
selection. The end-to-end acceptance test must boot the recovery UKI in a
Fedora 44 QEMU guest, read the persistent journal, select the prior successful
UKI, reboot, and verify the selected deployment becomes active.
