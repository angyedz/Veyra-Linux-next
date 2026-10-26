# Vpkg inside Vforge

The package-facing interface starts with `vforge`:

```sh
cd /path/to/project
vforge up
vforge install htop                         # current project environment
vforge remove htop
vforge search htop
vforge info htop
vforge create backend --network shared
vforge install --environment backend htop
vforge upgrade backend
```

Vforge routes these operations through the regular `vpkg` program inside the
environment. Entering an environment still lets you use `vpkg` directly:

```sh
vforge enter backend
vpkg search htop
vpkg install htop
vpkg remove htop
vpkg upgrade
```

For a one-off command, run it through `vforge exec`:

```sh
vforge exec backend -- vpkg install htop
```

Vpkg detects Vforge from its read-only root marker. Inside the environment,
`install`, `remove`, and `upgrade` use libdnf5 against that environment's own
RPM database and Fedora 44 / RPM Fusion repositories. Their writes go into
Vforge's persistent OverlayFS upper layer. The host package state is not used.
Veyra system-extension packages are not supported inside Vforge.

The environment runs on the active Veyra EROFS image as a read-only lower
layer. Vpkg checks that the marker matches the Veyra image and active dm-verity
root hash, and rejects a separate mount over `/usr`, the RPM database, or its
private state/cache. Invalid markers fail closed; they do not fall back to host
package handling.

An environment needs shared networking to download packages. For named
environments, request it when creating the environment:

```sh
vforge create backend --network shared
```

Project environments can request it in `.vforge.toml`:

```toml
[permissions]
network = "shared"
```

Package actions are limited to Fedora and RPM Fusion packages. Host-management
commands such as `vpkg system`, `stage`, `rollback`, and `channel` are blocked
inside Vforge.
