# Vforge package commands

Vforge is the package interface for named and project environments. These
commands act on the selected environment's private OverlayFS state, not on the
host system:

```sh
cd /path/to/project
vforge up
vforge install htop
vforge remove htop
vforge search htop
vforge info htop
```

The current project's environment is selected by default. A project can declare
its initial packages in `.vforge.toml`:

```toml
[packages]
system = ["python3", "nodejs", "git"]
```

Run `vforge up` to create or refresh that environment and install the declared
packages.

Named environments use the same commands:

```sh
vforge create backend --network shared
vforge install --environment backend htop
vforge search --environment backend htop
vforge upgrade backend
vforge enter backend
vforge exec backend -- rpm -q bash
```

Vforge calls the `vpkg` package backend inside the selected environment. That
backend uses libdnf5 to resolve Fedora 44 and RPM Fusion packages against the
environment's private RPM database. It is not a separate container or
environment manager; Vforge creates and owns the native OverlayFS environment.
No Podman daemon is involved.

The environment uses the active Veyra EROFS image as its read-only lower layer.
Vforge and the package backend verify that the root marker matches the running
Veyra image and dm-verity root hash. Package changes stay in the environment's
persistent OverlayFS upper layer. Veyra system-extension packages and host
management operations are not available inside an environment.

An environment needs shared networking to download packages. For a named
environment, request it when creating the environment:

```sh
vforge create backend --network shared
```

Project environments can request it in `.vforge.toml`:

```toml
[permissions]
network = "shared"
```
