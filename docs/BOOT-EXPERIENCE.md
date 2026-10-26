# Veyra Next boot presentation and performance

This is an **experimental** boot experience, not a proven boot-time optimization.

## Bootloader and diagnostics

Veyra Next uses **systemd-boot with UKIs**, not GRUB. The legacy
`branding/grub/theme.txt` is not the active boot menu and must not be
installed into the A/B image as if it were. This branch intentionally does
not replace the bootloader or change the update/recovery boot entries.

The kernel command line enables `quiet splash`, reduces informational
kernel/udev messages, and uses `systemd.show_status=auto`. Warnings and
critical failures remain available on the console and in the journal.
The Plymouth theme displays a static glacier-blue screen to avoid spending
CPU time on continuous animation. **A successful graphical Plymouth boot
has not yet been demonstrated in QEMU.** Verify that the selected theme is
actually present in the generated initrd before merging.

For troubleshooting, use the journal, `systemctl --failed`, and
`systemd-analyze`. Avoid disabling journald, NetworkManager, firewalld,
graphics initialization, or recovery services just to make boot look fast.

## Measure before optimizing

On a *booted test VM*, run:

```sh
python3 scripts/profile-boot.py --output ./boot-baseline
```

The profiler records `systemd-analyze time`, `critical-chain`, and
`blame`. Use a **new** output directory for each run: existing reports
are never overwritten. It does not stop services or edit the system.

Compare repeated boots with the same VM CPU, memory, disk, and firmware.
Review the critical chain before touching units; `blame` alone can be
misleading because services start in parallel. Measure again after each
change and keep a rollback path.

## Required release gates

1. Source checks and full mkosi image build pass.
2. Inspect the initrd to confirm the Veyra Plymouth script/theme is embedded.
3. Boot the generated image in QEMU/OVMF and inspect both successful
   graphical startup and an intentionally failing service.
4. Confirm the recovery entry and A/B update path remain available.
5. Record comparable cold-boot timings before claiming a speedup.

No host bootloader or disk partition changes are made by this branch.
