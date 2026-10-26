# Decision: use the running Veyra root as the vforge lower layer

Status: accepted for the native implementation; guest validation pending

The prior separate OCI-root design for Vforge is retired. Vforge bind-mounts
only the active read-only EROFS Veyra root as OverlayFS's lower layer. The bind
is non-recursive, so persistent machine-specific `/etc`, `/var`, and `/home`
mounts are excluded. Each environment owns persistent upper and work
directories selected by the user or stored in the project's `.vforge/`
directory. Linux user and mount namespaces provide the privilege and mount
boundary; PID and network namespaces are also created, with networking disabled
by default.

Vforge verifies the Fedora 44 Veyra image identity and the active dm-verity root
hash before creating the read-only marker consumed by `vpkg container`. Vpkg
uses libdnf5 against the environment's merged root and private RPM database.

This removes the separate Fedora root image, archive import flow, and container
engine dependency. Environment compatibility follows the Veyra base version:
Vforge does not yet track an environment across a system update or provide
snapshots and rollback. Container package changes currently update the active
upper in place.

Implementation status is deliberately limited. The CLI and source-level policy
checks exist, but OverlayFS operation, package changes, permissions, and
cross-update behavior still require guest validation and further work.
