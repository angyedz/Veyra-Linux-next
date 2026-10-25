# vforge development environments

**Status:** initial named-environment MVP. `vforge` manages persistent,
rootless Podman containers based on Fedora 44. Project environments and the
advanced permission model remain unimplemented.

`vforge` is the planned name of Veyra's development-environment manager. It
will cover two use cases without making either one mandatory:

1. **Project environment:** a project includes a small vforge configuration
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

The initial command set is:

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

Create uses the official Fedora 44 container image. The environment's writable
container layer persists in the user's rootless Podman storage between shells
and reboots. `enter` and `exec` start a stopped environment automatically;
leaving a shell does not stop it. Use `vforge stop` to stop it explicitly.

Network access is disabled at creation by default. Enable the rootless bridge
only when an environment needs network access:

```sh
vforge create web-dev --network bridge
```

`vforge` runs as the logged-in user and always selects local, rootless Podman.
Do not run it with `sudo`. Commands inside the container run as container root
within Podman's rootless user namespace, so install Fedora packages with
`dnf install ...` without `sudo`.

No host home or project directory, device, or host service socket is mounted
by default. Removing an environment requires an interactive confirmation and
deletes that environment's container state and installed packages; it does not
delete project files. The image uses the shared host kernel, so this boundary
is not equivalent to a virtual machine.

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
base image, so those versions are available directly on the host. In a vforge
environment, Fedora packages can install a separate Python or Node.js runtime;
`pip` dependencies should normally live in a Python virtual environment, and
`npm` dependencies in the project's `node_modules`. A user can choose host
development, a project-configured environment, or a persistent named
environment according to the project. vforge is not a requirement for using
these languages.

## Isolation boundary

vforge uses Fedora's maintained rootless container runtime rather
than implement a new container engine. Environments must not write to the host
`/usr`, RPM database, kernel, init system, bootloader or A/B slots. Project and
home directories should only be mounted where the user explicitly requests
them. Removing a named environment removes that environment's state, not the
project source tree. Containers share the host kernel; vforge is a development
environment boundary, not a virtual machine or a substitute for the OS update
and recovery security model.

vforge is separate from the host's Developer Mode. It remains the normal way
to create a mutable development environment without changing Veyra's system
files or disabling A/B updates.

This first slice intentionally supports only the fixed Fedora 44 image and
named container lifecycle. Project `.vforge.toml`, custom image selection,
project mounts, permission profiles, GPU/Wayland/audio access, snapshots,
upgrades, backup/restore, and compatibility with OTA system generations are
not implemented. Network is a create-time choice and cannot be changed by
`vforge` after creation. The command does not yet audit arbitrary Podman
configuration outside the options it sets; deployments that add global Podman
mounts or hooks need a separate security review before relying on this MVP for
untrusted projects.

The command wrapper tests skip outside QEMU/KVM, and the real Podman lifecycle
scenario refuses to run outside a VM. Run both as a non-root guest user from a
read-only source checkout mounted into a disposable Veyra VM:

```sh
scripts/test-vforge-qemu.sh
```

The integration scenario uses the real rootless Podman runtime to create an
environment, run a command through `vforge enter`, verify state through `exec`
after a stop/start cycle, and remove the environment. The guest needs outbound
network access on its first run to pull the Fedora 44 container image. The
Vforge container itself is created with network disabled. The scenario never
mounts the source checkout, Home, devices or sockets into that environment.
The unit tests mock Podman and check the wrapper's argument and safety
defaults; they do not qualify runtime isolation beyond the lifecycle exercised
by the QEMU scenario.
