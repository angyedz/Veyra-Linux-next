# vforge native environments

`vforge` creates persistent environments over the Veyra system root currently
running on the machine. The lower directory is `/`, mounted read-only in a
private mount namespace. OverlayFS directs writes into an environment's
persistent `upper` and `work` directories. Vforge does not import a separate
distribution root or use a container engine.

## Create and enter environments

```sh
vforge create backend --network shared
vforge install --environment backend htop
vforge enter backend
vforge exec backend -- rpm -q bash
vforge list
vforge remove --environment backend htop
vforge snapshot backend
vforge snapshots backend
vforge rollback backend
vforge upgrade backend
vforge delete backend
```

Named environments start with isolated networking. `vforge create backend --network shared` requests access to the host network and asks for confirmation.

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

[packages]
system = ["python3", "nodejs", "git"]

[permissions]
network = "shared"

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

Project networking is isolated unless `[permissions].network = "shared"` is
requested. The first request requires an interactive approval; the approval is
stored outside the project. Shared networking lets the environment reach the
internet and local network services. Removing the request in `.vforge.toml`
revokes it the next time the project is refreshed with `vforge up`.

`vforge install`, `vforge remove`, `vforge search`, and `vforge info` run the
matching regular `vpkg` command inside the selected environment. Use
`--environment NAME` for a named environment; without it, Vforge targets the
current project's environment. `vforge upgrade` updates a named environment
and checks that it is compatible with the currently running Veyra base.

`vforge up` installs each package listed in `[packages].system` by running
`vpkg install` inside the project environment. Package names are validated
and limited to Fedora/RPM Fusion packages. Removing a name from the list does
not automatically uninstall it yet; use `vforge remove PACKAGE` from the
project directory.

## Isolation and limits

Vforge starts user, mount, PID, and network namespaces. Networking is isolated
by default; an approved project may share the host network namespace. It creates
a private mount tree and uses the active read-only Veyra EROFS root as
OverlayFS's lower. Nested host mounts are detached from the lower view, so the
machine's persistent `/etc`, `/var`, and `/home` state is not exposed. The
environment's own persistent upper/work pair receives writes.
The process starts inside that merged root. Only the project and individually
approved mounts are exposed; `/proc`, a small `/dev`, `/tmp`, and `/run` are
provided privately for the process. Host environment variables are not passed
to commands; only locale and terminal settings are retained.

This is an early native implementation. Only the `integrated` project profile
is accepted today. GPU, Wayland, PipeWire, D-Bus, capability selection, seccomp
policy, SELinux policy, and package removal reconciliation are not implemented.
Snapshots copy the persistent OverlayFS upper into the same user-selected
storage, using filesystem reflinks when available and a regular copy otherwise.
They do not reserve disk space on a separate partition.

Vforge records the active Veyra image ID, version, and root digest when an
environment is first used. After an OTA changes that identity, `enter`, `exec`,
and package commands stop until `vforge upgrade NAME` snapshots the existing
state, runs `vpkg upgrade` against the new base, and records the new identity.
If that package operation fails, Vforge restores the saved upper. A snapshot can
be rolled back only while its recorded Veyra base is active; restore the system
deployment first to use a snapshot from an older base. Active environments
cannot be snapshotted, rolled back, upgraded, or deleted until their sessions
close.

Real namespace, OverlayFS, package, snapshot, and rollback behavior still needs
to pass the Fedora 44 Veyra guest workflow. A user namespace and rootless
OverlayFS support from the host kernel/storage filesystem are required;
Vforge reports the kernel or mount error when those prerequisites are missing.

## Verification status

Source tests cover persistent upper/work layout, project state placement,
read-only explicit mounts, network approval, reserved targets, Veyra root
identity, namespace selection, snapshot restore, and package command routing.
GitHub Actions runs those checks and builds the installed Veyra image directly.
A Fedora 44 Veyra guest run has not yet confirmed namespace creation, OverlayFS
mount, package installation, snapshots, rollback, or persistence across process
restarts.

Inside the environment, `vpkg install`, `remove`, and `upgrade` act on that
environment's private RPM database and OverlayFS upper. See
[`vpkg-vforge.md`](vpkg-vforge.md) for package command behavior.
