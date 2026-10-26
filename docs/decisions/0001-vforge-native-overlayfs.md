# Decision: use the running Veyra root as the vforge lower layer

Status: accepted for the native implementation

The prior separate-root design for vforge is retired. Vforge uses the actual `/`
from the running Veyra system as its read-only OverlayFS lower layer. Each
environment owns persistent upper and work directories selected by the user or
stored in the project's `.vforge/` directory. Linux user and mount namespaces
provide the privilege and mount boundary; PID and network namespaces are also
created, with networking disabled by default.

This removes the separate Fedora root image and archive import flow. Environment
compatibility follows the Veyra base version: Vforge does not yet track an
environment across a system update or provide snapshots and rollback.

Implementation status is deliberately limited. The CLI and source-level policy
checks exist, but OverlayFS operation, package changes, permissions, and
cross-update behavior still require guest validation and further work.
