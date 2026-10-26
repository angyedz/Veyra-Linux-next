# v-dri driver diagnostics

`v-dri status` is the first read-only slice of Veyra's driver manager. It
enumerates DRM cards from sysfs, reports PCI vendor/device IDs when available,
the bound kernel driver/module, and any module taint flags. `v-dri status
--json` prints schema-1 JSON for diagnostics and future integration.

Veyra's selected driver policy is Fedora in-tree drivers. The current
prototype does not download, install, sign, activate, or block kernel modules.
If Linux marks a loaded module with the `O` (out-of-tree) taint flag, v-dri
reports `external-module-detected` and exits with status 2. This is a diagnostic
signal, not enforcement: the current unsigned image does not prevent an
administrator from loading an external module. Module-signing enforcement and
Secure Boot trust are not implemented.

Exit codes:

- `0`: at least one DRM card is bound to a driver without an out-of-tree taint.
- `1`: sysfs or driver metadata could not be read safely.
- `2`: no DRM cards, an unbound card, or an out-of-tree module was found.

Unit tests use synthetic sysfs fixtures. They do not inspect or load drivers on
the development host. Fedora 44 QEMU validation with a virtual GPU is still
required; this diagnostic prototype does not establish real AMD/NVIDIA
compatibility or implement driver installation/activation.
