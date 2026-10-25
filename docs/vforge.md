# vforge development environments

`vforge` provides persistent named environments and project environments. It
uses rootless Podman with the fixed Fedora 44 image. Run the command as the
logged-in user, never with `sudo`; Podman runs with `--remote=false` and an
explicit per-user graphroot/runroot. The container shares the Veyra kernel and
is a development environment, not a VM or an absolute security boundary.

## Named environments

```sh
vforge create backend
vforge enter backend
vforge exec backend -- python3 --version
vforge snapshot backend before-upgrade
vforge upgrade backend
vforge rollback backend before-upgrade
vforge stop backend
vforge start backend
vforge delete backend
```

Network is disabled by default. `--network bridge` is an explicit, interactively
approved grant. `--home` accepts `none`, `project-only`, `read-only`, or
`read-write`; `project-only` does not mount the whole host Home. GPU, Wayland,
PipeWire and the user's session D-Bus socket require `--isolation trusted` and
interactive approval. The whole system D-Bus is never exposed. `strict` rejects
all host mounts/resources and does not use the Veyra `/usr` overlay.

The common named-environment root is user-selectable. By default it is
`$XDG_DATA_HOME/vforge`, or `~/.local/share/vforge` when XDG_DATA_HOME is unset.
Set and inspect the persistent root with:

```sh
vforge config show
vforge config storage-root /path/you/own/vforge
vforge --storage-root /another/path create scratch
vforge create --storage-root /another/path scratch
```

The persistent setting is saved in `$XDG_CONFIG_HOME/veyra/vforge.toml` (or
`~/.config/veyra/vforge.toml`). Storage uses ordinary free space on the selected
filesystem; there is no preallocation or separate partition. Symlinked storage
path components and roots not owned by the current user are rejected. Vforge
does not elevate privileges. An optional hard size cap is available with
`vforge create backend --size 20GiB` or `[environment].size = "20GiB"`. Vforge
stores that environment's Podman graphroot, Veyra upper/work directories and
snapshot data inside one sparse ext4 image mounted through `fuse2fs`; ext4
capacity (including filesystem metadata) is the cap, and the backing file
allocates host blocks only as they are written. It does not reserve the full
amount in advance. Small Vforge registry/config metadata outside the image is
not counted; explicitly approved host bind mounts, host Home mounts and
ephemeral tmpfs are also outside the cap because their data remains outside the
environment store. On a full image, package installation or snapshot creation
can fail with `ENOSPC`. The cap cannot be resized in place; delete and recreate
the environment to choose another size. An interrupted initial format can
leave an incomplete image requiring manual removal. The implementation requires
`e2fsprogs`, `/dev/fuse`, and a working rootless FUSE setup; this path still
needs validation in the Fedora 44 Veyra guest before runtime verification.
Changing the selected root does not move existing data; pass the old root
explicitly to continue using environments stored there. Project registry data
is kept under the user's XDG state directory, so changing the common root does
not hide project environments.

## Project environments

Put `.vforge.toml` in the project root, then run `vforge up` or `vforge enter`
from that directory. `vforge enter` with no name discovers the project config.
The config remains part of the project; generated state is kept separately in
`<project>/.vforge/`. Vforge adds `.vforge/` to the repository's local
`.git/info/exclude` so generated state is not staged. The project source is
indexed in the user's private XDG state directory so `list`, named `enter`,
and `delete` can find it without making generated state part of the repository.
The project directory is mounted at `/workspace` for integrated/trusted
profiles; `.vforge/` is hidden
inside the container with a private tmpfs and cannot be mounted by a custom
mount declaration. `strict` does not mount the project directory.

Example:

```toml
[environment]
name = "backend"
isolation = "integrated"

[packages]
system = ["python3", "nodejs", "git"]

[permissions]
network = "shared"
gpu = false
wayland = false
audio = false
system_bus = false

[permissions.home]
mode = "project-only"

[[permissions.mounts]]
source = "~/Documents/shared"
target = "/workspace/shared"
access = "read-only"
```

The parser rejects unknown keys, invalid package names, symlinked project
roots/config/mount-source components, protected host paths, unsafe/duplicate
container targets and mounts into the project state directory. Project
permissions are printed and explicitly approved on a TTY; approval is stored
outside the checkout and keyed to the permission digest. Expanding permissions
requires fresh approval. An existing project environment is checkpointed before
its configuration is applied. Project state can be removed with
`vforge delete <name>`; that deletes `.vforge/` data but leaves source and
`.vforge.toml` intact.

## Integrated filesystem and OTA compatibility

`integrated` and `trusted` use `fuse-overlayfs` with the current Veyra `/usr`
and host RPM database as read-only lowers and per-environment upper/work dirs.
Those uppers live under the named environment's data directory or the project
`.vforge/` directory. The container receives the merged `/usr` and merged RPM
database as writable mounts. Thus writes to those mounted system paths should
go to that environment's upper, while the Veyra lower remains unchanged. The
QEMU scenario checks the active Veyra `sysext-level` through the container,
checks that a `/usr` write appears only in the private upper, and checks that a
snapshot rollback restores that write.

The compatibility identity includes the active dm-verity `roothash`, distro
version, glibc/systemd RPM versions and Veyra sysext level. A changed root hash
blocks `enter`/`start` until `vforge upgrade` is run. Upgrade makes a snapshot,
temporarily connects the container to the bridge to run `dnf upgrade`, and
restores the checkpoint if that operation fails.

This is a deliberately bounded integrated slice, not the full lower-layer
model in the master specification: only `/usr` and the RPM database use Veyra
lowers. The container's Fedora 44 `/etc`, `/var`, `/bin`, `/lib` and other root
paths are not a filtered view of Veyra. Host `/etc` is not exposed because it
contains host secrets and mutable machine configuration. A filtered `/etc`
generation-specific lower and a coherent whole-root Veyra runtime remain
unimplemented; do not claim complete 6.1/6.2 integration until that boundary
is designed and tested.

Snapshots commit the container writable layer and copy the Veyra `/usr` and
RPM database uppers. Rollback first saves a recovery checkpoint, restores the
copy and recreates the container. On ordinary ext4/XFS these are space-using
file copies, not filesystem CoW snapshots; they can be large and are not an
atomic crash-consistent transaction across container metadata and both upper
trees. Size-capped environments include the snapshot images and Veyra upper
copies in the same ext4 capacity limit. A failed operation may need recovery
from the retained checkpoint.

## Verification status and limits

The wrapper tests and real lifecycle script are guarded to run only inside
QEMU/KVM with a Fedora 44 Veyra guest; the integration gate rejects Rawhide and
checks the environment's `/etc/os-release` is Fedora 44. Fedora 44 is the
current Veyra target, and these checks should change with an approved future
base update. The integration script also requires a non-root user and
read-only source checkout. Run `scripts/test-vforge-qemu.sh` from the
disposable Veyra guest after its image has been built. The QEMU test exercises rootless create,
enter/exec, an explicitly approved bridge-network `dnf install`, stop/start
persistence, Veyra lower visibility, RPM database and `/usr` upper isolation,
snapshot/rollback and deletion. The guest needs outbound network access to pull
the Fedora 44 image and package. A source-level test pass alone does not prove
the OverlayFS lifecycle or the Veyra-generation integration.

Vforge also relies on Podman defaults for user namespaces, seccomp and SELinux
policy. It does not yet audit every global Podman hook/configuration, expose
full Veyra `/etc`, automatically discover missing libraries, or provide
filesystem CoW/crash-atomic snapshots. Size caps use a private ext4 image rather
than filesystem quotas on arbitrary selected storage. These are explicit gaps;
profiles and device grants are not a claim of VM-grade isolation.
