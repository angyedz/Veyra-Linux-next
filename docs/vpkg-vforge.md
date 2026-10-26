# Vpkg backend in Vforge environments

Package management in a Vforge environment is exposed through `vforge`:

```sh
vforge install htop
vforge remove htop
vforge upgrade
vforge search htop
vforge info htop
```

For named environments, add `--environment NAME`. Without it, Vforge targets
the current project's `.vforge` environment. Vforge invokes the regular vpkg
package verbs inside that environment; vpkg detects and validates Vforge's
read-only root marker before selecting the environment's RPM database. Users
work through Vforge; vpkg remains the internal package backend.

The environment uses the active Fedora 44 Veyra EROFS image as its read-only
OverlayFS lower layer, with private persistent upper and work directories.
Vforge does not download a separate Fedora root.
Vpkg runs libdnf5 against `/` and keeps its RPM database, history, state, logs,
metadata, and package cache inside that environment's overlay.

Vforge requires UID 0 inside its user namespace. Vpkg checks the root marker,
the active Veyra image identity, and the dm-verity hash before any package
operation. It rejects separate mounts over `/usr`, the RPM database, or its
private state and cache. Package transactions currently change that
environment's active upper in place; isolated package generations and package
rollback are not implemented yet.

Project environments use `/path/to/project/.vforge`. Named environments use
the storage root selected by the user. Package changes stay in the selected
environment and do not modify the host's RPM database.
