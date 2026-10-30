# vforge native environments

`vforge` creates persistent environments over the Veyra system root currently
running on the machine. A small system service exposes the active EROFS root at
`/run/veyra/vforge/base` as a read-only, non-recursive bind mount. In the user's
private mount namespace, rootless OverlayFS uses that base as its lower layer
and directs most writes into the environment's persistent `upper` and `work`
directories. Before attaching OverlayFS it seeds user-owned directory metadata
for `/usr`, `/usr/share`, `/usr/bin`, `/usr/lib`, `/usr/lib64`, `/usr/local`,
`/etc`, `/opt`, and `/root` **inside the private upper only**. This lets
rootless commands create files under those paths without trying to write through
the unmapped host-root-owned EROFS directory inodes. The original lower tree is
still merged and untouched; preexisting unsafe symlinks/permissions fail closed. A separate private, persistent `var` directory under each
environment is bind-mounted at its guest `/var`. Fedora's EROFS `/var`
may be host-root-owned and unwritable in the rootless user namespace; this
bind avoids the real guest `EACCES: /var/lib` failure while retaining Vforge's
private vpkg history and cache. The bind mount leaves the host's persistent
`/etc`, `/var`, and `/home` mounts outside the environment. Vforge does
not import a separate distribution root or use Podman.

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
metadata, OverlayFS upper layer, work directory, and private `var`
directory under `<storage-root>/environments/<name>/`.

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
permission to bind the host's namespace root. This non-recursive bind excludes
the machine's persistent `/etc`, `/var`, and `/home` mounts. The environment's
own persistent upper/work pair receives writes.
The process starts inside that merged root. Only the project and individually
approved mounts are exposed; `/proc`, a small `/dev`, `/tmp`, and `/run` are
provided privately for the process. Host environment variables are not passed
to commands; only locale and terminal settings are retained.

This is an early native implementation. Only the `integrated` project profile
is accepted today. GPU, Wayland, PipeWire, D-Bus, shared networking, capability
selection, seccomp policy, SELinux policy, snapshot/rollback, automatic base
compatibility management, and project package reconciliation are not
implemented. Real guest validation is limited to rootless OverlayFS
creation, lower identity, write persistence, host isolation, and deletion;
package installation *inside Vforge* is not yet validated. A user namespace and
rootless OverlayFS supporting the host kernel/storage filesystem are required;
Vforge reports the kernel or mount error when those prerequisites are unavailable.

Vforge environments are compatible with the Veyra root from which they were
created. After a Veyra system update, review or recreate environments before
using them. Do not assume an upper layer is compatible with every system
version.

## Verification status

In signed OTA QEMU run `40670181999` (2026-10-28), the serial artifact
`uefi-ota-boot-diagnostics/boot-b.log` **confirmed real Fedora 44 guest**
`VEYRA_VFORGE_CI_FIRST_EXEC_PASS`, `VEYRA_VFORGE_CI_SECOND_EXEC_PASS`, and
`VEYRA_VFORGE_CI_DELETE_PASS`. The guest proved:
- rootless native OverlayFS with the running Veyra EROFS lower;
- persistence of a private `/usr/share` marker across separate `vforge exec` sessions;
- absence of the marker on the host filesystem;
- cleanup of the restricted kernel-created `work/work` staging directory.

This followed previous failures on `/proc/self/fd/3` (mount source descriptor),
`/var/lib` (unmapped EROFS lower parent), `/usr/share` (mapped ownership),
and `work/work` (kernel's restricted private overlay staging directory).
Each had a targeted change; their real-QEMU smoke test is now green.

**The full signed vpkg/OTA integration is not yet verified.** The next guest
stage failed while running its source tests because the fixture injected only
a partial repository snapshot (24 missing-file errors). The OTA test setup
now exports Git-tracked `HEAD` sources, excluding build output and untracked
local files; the full guest re-run is pending. A green source test suite
alone does **not** establish success of vpkg transactions or A/B rollback.
