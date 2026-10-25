# Vforge development environments

**Status:** product design; no `vforge` command or runtime integration is
implemented yet.

`vforge` is the planned name of Veyra's development-environment manager. It
will cover two use cases without making either one mandatory:

1. **Project environment:** a project includes a small Vforge configuration
   describing its base image, packages, environment variables and explicitly
   mounted directories. A developer enters or runs commands in that environment
   from the project directory. The configuration can be committed with the
   source so teammates can recreate the toolchain.
2. **Persistent named environment:** a user creates an environment such as
   `web-dev`, `rust`, or `python-data` independently of any project. It has a
   long-lived writable environment, so packages installed inside it and its
   settings persist between sessions and host reboots. It can be entered from
   any directory, renamed, stopped, started, upgraded and removed without
   changing the host OS. A project directory is mounted only when the user
   explicitly selects it.

The first command shape is:

```sh
vforge create web-dev
vforge list
vforge enter web-dev
vforge exec web-dev -- node --version
vforge stop web-dev
vforge start web-dev
vforge rename web-dev web-dev-old
vforge delete web-dev
```

Entering an environment is the normal place to install a toolchain with Fedora
packages, for example `dnf install nodejs python3`. Those changes belong to the
named environment; they do not update Veyra's host RPM database or system
image. `vpkg` remains the route for applications installed into Veyra's shared
app layer.

For a project environment, a project-local config will select the environment
definition and provide the corresponding run/enter command. The config format
and final command names are still to be implemented and documented; the example
above is the agreed direction, not a claim that these commands currently work.

## Language tools and packages

The current image recipe includes Python, pip, Node.js and npm in the Fedora
base image, so those versions are available directly on the host. In a Vforge
environment, Fedora packages can install a separate Python or Node.js runtime;
`pip` dependencies should normally live in a Python virtual environment, and
`npm` dependencies in the project's `node_modules`. A user can choose host
development, a project-configured environment, or a persistent named
environment according to the project. Vforge is not a requirement for using
these languages.

## Isolation boundary

The manager should use Fedora's maintained rootless container runtime rather
than implement a new container engine. Environments must not write to the host
`/usr`, RPM database, kernel, init system, bootloader or A/B slots. Project and
home directories should only be mounted where the user explicitly requests
them. Removing a named environment removes that environment's state, not the
project source tree. Containers share the host kernel; Vforge is a development
environment boundary, not a virtual machine or a substitute for the OS update
and recovery security model.

The first implementation still needs a precise command contract for image
selection, image upgrades, persistent storage, network access, project mounts
and backup/restore. These examples describe the intended behavior; Vforge is
still a design and no `vforge` command or runtime integration exists yet.
