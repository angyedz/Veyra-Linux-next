# Vforge development environments

Vforge creates persistent rootless Podman environments from an imported Veyra OCI
rootfs. The artifact is built from the same Fedora 44 mkosi image definition as
the installed Veyra system; Vforge does not compose a Fedora container with
host `/usr` or RPM database mounts. Run Vforge as the logged-in user, never with
`sudo`.

Import the OCI archive produced by the Veyra image build before creating an
environment:

```sh
vforge base import /path/to/veyra-oci-rootfs.oci.tar
vforge base status
vforge create backend
vforge enter backend
vforge exec backend -- vpkg container --help
```

Import validates the Veyra image labels, Fedora 44 `/etc/os-release`, image
version and manifest digest, private `/usr/lib/sysimage/rpm`, and the container
Vpkg API. It rejects a Fedora-only image. There is not yet an automatic release
or download path; users must provide the built OCI archive. Vforge requires
rootless Podman's `overlay` graph driver. It keeps the OCI base and environment
layer in the selected Podman graphroot, with an immutable base and a persistent
Podman-managed writable layer. No host `/etc`, `/usr`, RPM DB, `/var`, or root
filesystem is mounted into the container.

## Environments

```sh
vforge create backend
vforge enter backend
vforge exec backend -- vpkg container install ripgrep
vforge snapshot backend before-upgrade
vforge upgrade backend
vforge rollback backend before-upgrade
vforge stop backend
vforge start backend
vforge delete backend
```

Project environments are configured in `.vforge.toml` and started with
`vforge up`. Project source and explicitly approved user resources are bind
mounts; they remain outside rootfs snapshots. `vforge upgrade` takes a snapshot,
connects the environment to the bridge if necessary, runs
`vpkg container upgrade` inside the Veyra root, and rolls back to the checkpoint
if the transaction fails. Package commands run inside the container namespace,
where container-mode Vpkg may execute RPM scriptlets. Vpkg state, RPM DB, and
cache belong to that rootfs environment.

The root identity is mounted read-only at `/run/veyra-vforge/root.json`. Its
schema-1 marker records the Veyra image id/version, manifest digest, a stable
per-environment root id, `storage_driver=podman-overlay`, and
`immutable_base=true`. Vforge checks the marker and Podman root-id/base-digest
labels before reuse. The marker file is owned by the invoking user on the host;
rootless Podman's user namespace maps that owner to container uid 0. Mounts under
`/etc`, `/usr`, `/var`, or `/boot` are rejected.

`strict` forbids host mounts, Home access, and exposed devices/sockets.
`integrated` allows the project and explicitly configured mounts.
`trusted` additionally permits individually approved GPU, Wayland, PipeWire,
user session D-Bus, or Home access. Network is disabled by default; requesting
bridge access requires interactive approval. Containers drop all Podman
capabilities and re-add only the set required for RPM file ownership, metadata,
and in-root scriptlets. These capabilities stay within rootless Podman's user
namespace; no host privilege is granted.

The common data root defaults to `$XDG_DATA_HOME/vforge` or
`~/.local/share/vforge`; configure it with `vforge config storage-root PATH`.
Project state lives in `<project>/.vforge/` and is excluded via
`.git/info/exclude`. Optional `--size` storage uses a sparse ext4 image mounted
through `fuse2fs`; it limits Podman storage and snapshots in that environment,
but not explicit host bind mounts. It requires `e2fsprogs`, `/dev/fuse`, and
working rootless FUSE support. A full image can cause package transactions or
snapshots to fail with `ENOSPC`; resizing requires deleting and recreating the
environment.

## Snapshots and compatibility

Vforge snapshots use `podman container commit` to capture the whole writable
container root, then recreate the container from the committed image during
rollback. Each snapshot records the environment's root id, Veyra base digest,
and image version; rollback refuses snapshots whose identity does not match the
current environment. Project/Home/device/socket mounts are external and are not
captured. Snapshot recovery and atomicity have source-level tests, but need
runtime validation under Podman/QEMU before they can be treated as durable
against power loss.

Environments made by the older Fedora-root/host-overlay prototype are rejected
by start, snapshot, rollback, upgrade, and project update. There is no implicit
migration because those containers do not contain a complete Veyra root.
Delete and recreate them after importing a valid Veyra OCI archive.

## Verification status and limits

Local source tests cover identity and marker policy, creation arguments, Vpkg
command routing, snapshot metadata, rollback identity checks, and refusal of
legacy roots. GitHub Actions builds and checks the OCI artifact. The QEMU
fixture now consumes the OCI archive from that same build and checks rootless
Podman creation, marker visibility, a real `vpkg container install`, private RPM
DB isolation, and snapshot/rollback. The Actions job runs it only when the
runner exposes KVM. No Podman/QEMU runtime success is claimed until that guest
scenario completes successfully.

Vforge uses the Veyra host kernel and rootless Podman, not a VM. Its isolation
still depends on the host's Podman, user namespaces, seccomp, SELinux and storage
configuration. An OCI container is not an absolute security boundary.
