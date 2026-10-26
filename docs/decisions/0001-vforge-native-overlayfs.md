# Decision: use the running Veyra root as the vforge lower layer

Status: accepted for the native implementation; Fedora 44 guest validation pending

Vforge uses no Podman or separate Fedora container root. It bind-mounts only the
active read-only EROFS Veyra root as OverlayFS's lower layer, then runs commands
inside that merged root. The bind is non-recursive, so machine-specific `/etc`,
`/var`, and `/home` mounts are excluded. Each environment has persistent upper
and work directories in the user-selected shared storage location or the
project's `.vforge/` directory. Linux user and mount namespaces provide the
privilege and mount boundary; PID and network namespaces are also created,
with networking disabled by default.

Vforge verifies the Fedora 44 Veyra image identity and the active dm-verity root
hash before creating the read-only marker consumed by `vpkg`. Vpkg
uses libdnf5 against the environment's merged root and private RPM database.

Package commands run Veyra's `vpkg` against the environment's merged root and
private RPM database. Snapshots save the OverlayFS upper using reflinks when
available and a regular copy otherwise; they use the user's selected storage
and do not reserve space on a separate partition.

Every environment records the active Veyra image ID, version, and root digest.
After an OTA changes that identity, entry and package operations stop until
`vforge upgrade` snapshots the environment, runs `vpkg upgrade` on the new
base, and records the new identity. If the package operation fails, Vforge
restores the prior upper. A snapshot can be restored only while its recorded
base identity is active.

The branch includes source policy checks and a Fedora 44 QEMU scenario for
native OverlayFS, Vforge package commands, project packages, snapshots, and
OTA compatibility. The scenario must pass before those runtime behaviors are
considered verified. GPU, Wayland, PipeWire, D-Bus, capability selection,
seccomp, and SELinux integration remain incomplete.
