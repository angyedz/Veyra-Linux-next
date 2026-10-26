# vforge native environments

`vforge` creates persistent environments over the Veyra system root currently
running on the machine. A small system service exposes the active EROFS root at
`/run/veyra/vforge/base` as a read-only, non-recursive bind mount. In the user's
private mount namespace, rootless OverlayFS uses that base as its lower layer
and directs writes into the environment's persistent `upper` and `work`
directories. The bind mount leaves the host's persistent `/etc`, `/var`, and
`/home` mounts outside the environment. Vforge does not import a separate
distribution root or use Podman.

## Create and enter environments

```sh
vforge create backend
vforge enter backend
vforge exec backend -- rpm -q bash
vforge list
vforge delete backend
```

The default storage root is `$XDG_DATA_HOME/vforge` or
`~/.local/share/vforge`. Choose another persistent location with
`vforge config storage-root /path/to/vforge-data` or
`VFORGE_STORAGE_ROOT=/path/to/vforge-data`. Each named environment stores its
metadata, OverlayFS upper layer, and work directory under
`<storage-root>/environments/<name>/`.

Project environments use `.vforge/` in the project directory. `vforge up`
creates or refreshes the environment from `.vforge.toml`; `vforge enter` with no
name enters the current project's environment. Project source is mounted at
`/workspace`, and project state is hidden from that bind mount while running.
The project directory remains outside the environment's OverlayFS layer.

Example configuration:

```toml
[environment]
name = "backend"
isolation = "integrated"

[permissions.home]
mode = "project-only"

[[permissions.mounts]]
source = "~/Documents/shared"
target = "/workspace/shared"
access = "read-only"
```

Additional host mounts must use existing directories and safe absolute targets.
They require interactive approval when first requested and again if the project
configuration adds a mount. Mounts are read-only unless `access =
"read-write"` is specified. Reserved system paths and paths that escape `/` are
rejected. The whole host Home is never mounted.

## Isolation and limits

Vforge starts user, mount, PID, and network namespaces. Networking is disabled.
The system service prepares a read-only bind of only the active EROFS root
before user sessions start. Vforge binds that stable mount into its private
namespace and mounts kernel OverlayFS with `userxattr`, so it does not need
permission to bind the host's namespace root. The machine's persistent `/etc`,
`/var`, and `/home` state is not exposed. The environment's own persistent
upper/work pair receives writes.
The process starts inside that merged root. Only the project and individually
approved mounts are exposed; `/proc`, a small `/dev`, `/tmp`, and `/run` are
provided privately for the process. Host environment variables are not passed
to commands; only locale and terminal settings are retained.

This is an early native implementation. Only the `integrated` project profile
is accepted today. GPU, Wayland, PipeWire, D-Bus, shared networking, capability
selection, seccomp policy, SELinux policy, snapshot/rollback, automatic base
compatibility management, and project package reconciliation are not
implemented. Package installation and real OverlayFS behavior have not yet
been validated in a Veyra guest. A user namespace and rootless OverlayFS
supporting the host kernel/storage filesystem are required; Vforge reports the
kernel or mount error when those prerequisites are unavailable.

Vforge environments are compatible with the Veyra root from which they were
created. After a Veyra system update, review or recreate environments before
using them. Do not assume an upper layer is compatible with every system
version.

## Verification status

Source tests cover persistent upper/work layout, project state placement,
read-only explicit mounts, reserved targets, Veyra root identity, and namespace
selection. GitHub Actions runs those checks and builds the installed Veyra
image directly. A Fedora 44 Veyra guest run
has not yet confirmed namespace creation, OverlayFS mount, package installation,
or persistence across process restarts.
