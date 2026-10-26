# Vpkg in Vforge environments

`vpkg container install NAME...`, `vpkg container remove NAME...`, and
`vpkg container upgrade` manage RPMs in the current Vforge environment. They
run libdnf5 against `/` and the environment's private RPM database. The root is
the active Fedora 44 Veyra EROFS system image, mounted read-only as OverlayFS's
lower layer, with the environment's own persistent upper and work directories.
Vforge does not create a separate Fedora root or use a container engine.

Vpkg requires UID 0 inside the Vforge user namespace. The EROFS lower keeps the
base system read-only; package writes go to that environment's OverlayFS upper.
Vforge intentionally excludes the host's persistent `/etc`, `/var`, and `/home`
mounts from the lower. Vpkg rejects separate mounts over `/usr`, the RPM
database, or its private state and cache.

To download packages from Fedora or RPM Fusion, a named environment can be
created with `vforge create NAME --network shared`. For a project, request shared
network access in `.vforge.toml` and approve it when running `vforge up`:

```toml
[permissions]
network = "shared"
```

Without that approval, the environment has its own isolated network namespace.

## Vforge marker contract

Vforge creates a root-owned, read-only bind mount at
`/run/veyra-vforge/root.json`. Vpkg verifies the marker's mount and file modes,
then checks its identity against `/usr/lib/os-release` and the active kernel
`roothash=` in the environment.
Required JSON fields:

```json
{
  "schema": 1,
  "kind": "vforge-veyra-root",
  "root_id": "<32 lowercase hexadecimal characters, unique per environment>",
  "image_id": "veyra-next",
  "image_version": "<same as IMAGE_VERSION in the rootfs>",
  "base_digest": "<active 64-character dm-verity roothash>",
  "storage_driver": "overlayfs",
  "immutable_base": true
}
```

The rootfs must report `ID=fedora`, `VERSION_ID=44`, `IMAGE_ID=veyra-next`,
and the matching `IMAGE_VERSION`. The marker's base digest comes from the
running kernel's `roothash=` command-line value. Vforge refuses to create the
marker if the active root is not a read-only EROFS Veyra deployment.

All mutable package data remains inside the environment's root overlay:

- RPM database: `/usr/lib/sysimage/rpm`
- libdnf history, persistence, state, and logs: `/var/lib/veyra/vpkg/container/`
- libdnf metadata and package cache: `/var/cache/veyra/vpkg/container/`

RPM scriptlets and triggers run in Vforge's user, mount, PID, and network
namespaces against that environment's writable upper. This mode currently
updates the environment's active upper in place; isolated package generations
and package rollback are not implemented here yet. The host package paths
retain their existing transaction policy.
