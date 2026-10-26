# Developer Mode

**Status:** design only. The current image does not expose a Developer Mode
switch, writable system layer, or update guard.

## Purpose

Developer Mode is an explicit option for people who need to change the
installed Veyra host directly: install or replace system RPMs, edit files
under `/usr`, try drivers, or change system services. It is not required for
ordinary development; [Vforge](vforge.md) provides persistent and project-local
environments without making the host writable.

## Proposed implementation

Keep the signed A/B slot as the immutable lower layer. Fedora 44 ships systemd
259, whose `systemd-sysext` supports mutable mode. When Developer Mode is
enabled, configure `/etc/systemd/sysext.conf` with:

```ini
[SysExt]
Mutable=yes
```

`systemd-sysext` then permits writes to merged `/usr` and `/opt` and routes
them to `/var/lib/extensions.mutable/usr` and
`/var/lib/extensions.mutable/opt`. Those paths live on Veyra's persistent
`/var` partition, so host package changes survive reboot and remain separate
from both EROFS A/B roots. Enabling the mode must also create a small
`veyra-dev-mode` directory extension in `/var/lib/extensions/` with matching
extension-release metadata and a visible marker file. This makes
systemd-sysext establish the mutable `/usr` and `/opt` overlay even on a fresh
system with no other extensions installed. Fedora's [systemd 259
documentation](https://www.freedesktop.org/software/systemd/man/259/systemd-sysext.html)
describes these write-routing directories and the `Mutable=yes` setting.

The user-facing control is planned as:

```text
sudo veyra-dev-mode enable
veyra-dev-mode status
sudo veyra-dev-mode disable
```

The final command names are not fixed. Enabling must require administrator
authentication and refuse to proceed while an A/B trial update is pending.
Veyra's updater must refuse update checks that lead to staging, update
installation and reboot into a staged update while this mode is active.

## Warning and recovery

Before enabling the mode, show a clear warning that direct system changes can
break boot, graphics, networking, package management or recovery. Keep a
visible desktop indicator and show the warning again in the terminal so the
mode is not easy to forget.

Disabling the mode changes `Mutable` back to `no` and refreshes systemd-sysext,
so the clean A/B root is visible again. The mutable data must not be silently
deleted: offer to archive it for inspection or explicitly discard it. Normal
A/B updates resume only after the mutable layer is no longer active.

Recovery must include a boot path that ignores the mutable layer, even if that
layer prevents the regular desktop from starting. From that clean path, users
can archive or reset `/var/lib/extensions.mutable/{usr,opt}` and return to the
known-good A/B system. Recovery must not need the broken mutable `/usr` to run.

## Guarantees and limits

- Developer Mode does not rewrite or re-sign an A/B root. dm-verity still
  checks the lower image, but it does not authenticate files supplied by the
  mutable upper layer.
- Veyra-managed system updates are blocked while the mode is active. A root
  administrator can still invoke lower-level system tools directly; the mode
  is an explicit administrator choice, not a security boundary against root.
- Package and driver changes made directly in the host are not transactionally
  protected. They can leave the installed system unusable until Recovery
  disables or resets the mutable layer.
- Vforge remains rootless and independent. Enabling Developer Mode is never a
  prerequisite for installing project toolchains or using named environments.

## Required validation before release

Exercise the mode in a Fedora 44 VM: enable it, install and remove an RPM,
confirm the changes survive reboot, prove that Veyra's updater refuses to stage
an A/B update, boot Recovery without the mutable layer, and return to the clean
slot while preserving or deliberately discarding the archived changes.
