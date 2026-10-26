# Vpkg in Vforge containers

`vpkg container install NAME...`, `vpkg container remove NAME...`, and
`vpkg container upgrade` manage RPMs in the current container's Veyra OCI
rootfs. The commands run libdnf5 with `/` as its installroot. They do not use
the host's native generations, extension directories, or RPM database.

The container must run `vpkg` as UID 0 in its root namespace. Its immutable
base is a Fedora 44 Veyra OCI image; Podman provides one persistent writable
overlay per environment in the user's selected container storage. Vpkg rejects
a root where `/usr`, the RPM database, or Vpkg state/cache have been mounted
separately. In particular, a Fedora root with a host `/usr` overlay is not a
supported target.

## Vforge marker contract

Vforge bind-mounts a root-owned read-only marker at
`/run/veyra-vforge/root.json`. Vpkg verifies that the marker itself is a
read-only mount and that the identity agrees with `/usr/lib/os-release` in the
rootfs. Required JSON fields:

```json
{
  "schema": 1,
  "kind": "vforge-veyra-root",
  "root_id": "<32 lowercase hexadecimal characters, unique per environment>",
  "image_id": "veyra-next",
  "image_version": "<same as IMAGE_VERSION in the rootfs>",
  "base_digest": "<64 lowercase hex SHA-256 OCI manifest digest>",
  "storage_driver": "podman-overlay",
  "immutable_base": true
}
```

The optional `mount_program` field is accepted only with value `fuse-overlayfs`
when Podman reports it. The rootfs must report `ID=fedora`, `VERSION_ID=44`,
`IMAGE_ID=veyra-next`, and the matching `IMAGE_VERSION`. Vpkg also requires the
private RPM database at `/usr/lib/sysimage/rpm`.

All mutable package data stays inside the rootfs overlay:

- RPM database: `/usr/lib/sysimage/rpm`
- libdnf history, persistence, state, and logs: `/var/lib/veyra/vpkg/container/`
- libdnf metadata and package cache: `/var/cache/veyra/vpkg/container/`

The identity marker asserts which immutable Veyra image Vforge selected; it
does not grant permission to change the host. Vforge owns environment creation,
overlay persistence, and marker lifecycle.

RPM scriptlets and triggers run for container transactions so packages can
complete their normal setup. They execute in the rootless Podman container's
namespaces against that environment's writable overlay.
The host and native Vpkg package paths keep scriptlet suppression and their
existing safety policy.
