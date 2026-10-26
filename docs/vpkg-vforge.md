# Vforge package commands

Vforge is the package interface for named and project environments. These
commands act on the selected environment's private OverlayFS state, not on the
host system:

```sh
cd /path/to/project
vforge up
vforge install htop
vforge remove htop
vforge upgrade
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

Vforge uses its internal libdnf5 package backend to resolve Fedora 44 and
RPM Fusion packages against the environment's private RPM database. Vforge
creates and owns the native OverlayFS environment, and package changes do not
affect the host.

The environment uses the active Veyra EROFS image as its read-only lower layer.
Vforge and the package backend verify that the root marker matches the running
Veyra image and dm-verity root hash. Package changes stay in the environment's
persistent OverlayFS upper layer. Veyra system-extension packages and host
management operations are not available inside an environment. Package
transactions currently update that upper layer in place; isolated package
generations and package rollback are not implemented yet.

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

For named environments, add `--environment NAME`. Without it, Vforge targets
the current project's `.vforge` environment. Vforge invokes the regular package
verbs through its internal vpkg backend, which validates the read-only root
marker before selecting the environment's RPM database. It rejects separate
mounts over `/usr`, the RPM database, or its private state and cache. Vforge
requires UID 0 inside its user namespace. Project environments live in
`/path/to/project/.vforge`; named environments use the storage root selected by
the user. Package changes stay in the selected environment and do not modify
the host's RPM database.
