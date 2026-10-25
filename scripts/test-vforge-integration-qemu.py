#!/usr/bin/python3
"""Exercise a real rootless Vforge lifecycle inside a disposable Veyra VM."""

from __future__ import annotations

import os
import pathlib
import pty
import select
import shutil
import errno
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import uuid
from importlib.machinery import SourceFileLoader


def require_qemu_guest() -> None:
    try:
        virtualizer = subprocess.run(
            ["systemd-detect-virt", "--vm"], check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("could not verify the QEMU/KVM guest") from error
    if virtualizer not in {"qemu", "kvm"}:
        raise RuntimeError(f"Veyra integration tests must run inside QEMU/KVM, detected {virtualizer!r}")
    if os.geteuid() == 0:
        raise RuntimeError("run this scenario as a normal guest user so Podman stays rootless")
    host_release = os_release_fields(pathlib.Path("/etc/os-release").read_text(encoding="utf-8"))
    if host_release.get("VERSION_ID") != "44" or "rawhide" in host_release.get("VERSION_CODENAME", "").lower():
        raise RuntimeError(f"Vforge QEMU tests require a validated Fedora 44 Veyra guest, found {host_release!r}")
    root_filesystem = subprocess.run(
        ["findmnt", "-n", "-o", "FSTYPE", "--target", "/"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    require_veyra_ero_root(root_filesystem)

    checkout = pathlib.Path(__file__).resolve().parents[1]
    try:
        options = subprocess.run(
            ["findmnt", "-n", "-o", "OPTIONS", "--target", str(checkout)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip().split(",")
    except (OSError, subprocess.CalledProcessError) as error:
        raise RuntimeError("could not verify the source checkout mount") from error
    if "ro" not in options:
        raise RuntimeError("mount the source checkout read-only inside the disposable QEMU guest")


def require_veyra_ero_root(filesystem: str) -> None:
    if filesystem != "erofs":
        raise RuntimeError(f"Vforge integration needs the current EROFS Veyra lower, found root filesystem {filesystem!r}")


def os_release_fields(contents: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in contents.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value.strip().strip('"').strip("'")
    return fields


def directory_digest(root: pathlib.Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix().encode()
        digest.update(relative + b"\0")
        if path.is_symlink():
            digest.update(b"L" + os.readlink(path).encode())
        elif path.is_file():
            digest.update(b"F" + path.read_bytes())
        elif path.is_dir():
            digest.update(b"D")
    return digest.hexdigest()


def exhaust_store_until_enospc(root: pathlib.Path) -> pathlib.Path:
    filler = root / ".vforge-enospc-probe"
    descriptor = os.open(filler, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    chunk = b"\0" * (4 * 1024 * 1024)
    exhausted = False
    try:
        while True:
            try:
                written = os.write(descriptor, chunk)
                if written <= 0:
                    raise RuntimeError("storage-cap probe made no forward progress")
            except OSError as error:
                if error.errno != errno.ENOSPC:
                    raise
                exhausted = True
                break
        os.fsync(descriptor)
    except OSError as error:
        if error.errno == errno.ENOSPC:
            exhausted = True
        else:
            raise
    finally:
        os.close(descriptor)
    if not exhausted:
        raise RuntimeError("writing into the capped Vforge store did not reach ENOSPC")
    return filler


def require_enospc_probe(result: subprocess.CompletedProcess[str]) -> None:
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        raise RuntimeError("vforge exec probe command did not complete successfully")
    if "VFORGE_ENOSPC_PROBE_EXECUTED" not in output:
        raise RuntimeError("ENOSPC probe did not reach the guest shell")
    if "no space left on device" not in output.lower():
        raise RuntimeError("integrated system write did not fail with ENOSPC")


def run(command: list[str], *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def run_with_tty(command: list[str], input_text: str, *, cwd: pathlib.Path | None = None, timeout: int = 90) -> str:
    master, slave = pty.openpty()
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, close_fds=True, cwd=cwd)
    os.close(slave)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        os.write(master, input_text.encode())
        while process.poll() is None:
            if time.monotonic() >= deadline:
                process.kill()
                raise RuntimeError(f"timed out running {' '.join(command)}")
            readable, _, _ = select.select([master], [], [], 0.2)
            if readable:
                try:
                    block = os.read(master, 4096)
                except OSError:
                    block = b""
                if block:
                    output.extend(block)
                    sys.stdout.buffer.write(block)
                    sys.stdout.buffer.flush()
        if process.returncode != 0:
            raise RuntimeError(f"{' '.join(command)} exited with status {process.returncode}")
        return output.decode(errors="replace")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


def container_exists(name: str, graphroot: pathlib.Path | None = None) -> bool:
    result = run(["podman", "--remote=false", *podman_roots(graphroot), "container", "exists", name], check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RuntimeError(f"could not inspect Podman container {name} (status {result.returncode})")


def vforge_store() -> pathlib.Path:
    return pathlib.Path(run(["vforge", "config", "show"], capture=True).stdout.strip())


def load_vforge_module():
    executable = shutil.which("vforge")
    if executable is None:
        raise RuntimeError("could not locate the Vforge implementation for runtime metadata checks")
    loader = SourceFileLoader("veyra_vforge_guest_test", executable)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None:
        raise RuntimeError("could not load the Vforge implementation for runtime metadata checks")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def podman_roots(graphroot: pathlib.Path | None = None) -> list[str]:
    graphroot = graphroot or (vforge_store() / "containers")
    graphroot.mkdir(mode=0o700, parents=True, exist_ok=True)
    runtime = pathlib.Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/vforge-{os.getuid()}")) / "vforge"
    runroot = runtime / hashlib.sha256(str(graphroot).encode()).hexdigest()[:16]
    runroot.mkdir(mode=0o700, parents=True, exist_ok=True)
    return ["--root", str(graphroot), "--runroot", str(runroot)]


def delete_environment(name: str) -> None:
    run_with_tty(["vforge", "delete", name], "yes\n")


def main() -> int:
    xdg = tempfile.TemporaryDirectory(prefix="vforge-qemu-xdg-")
    old_xdg = {key: os.environ.get(key) for key in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME")}
    try:
        require_qemu_guest()
        os.environ["XDG_DATA_HOME"] = str(pathlib.Path(xdg.name) / "data")
        os.environ["XDG_CONFIG_HOME"] = str(pathlib.Path(xdg.name) / "config")
        os.environ["XDG_STATE_HOME"] = str(pathlib.Path(xdg.name) / "state")
        if not shutil.which("vforge") or not shutil.which("podman"):
            raise RuntimeError("the Veyra guest must contain both vforge and Podman")
        vforge_module = load_vforge_module()

        rootless = run(
            ["podman", "--remote=false", "info", "--format", "{{.Host.Security.Rootless}}"],
            capture=True,
        ).stdout.strip()
        if rootless.lower() != "true":
            raise RuntimeError(f"Podman is not using its rootless store (reported {rootless!r})")
        rpmdb_path = pathlib.Path(run(["rpm", "--eval", "%{_dbpath}"], capture=True).stdout.strip())
        rpmdb_before = directory_digest(rpmdb_path)

        suffix = uuid.uuid4().hex[:10]
        name = f"qemu-vforge-{os.getuid()}-{suffix}"
        marker = f"vforge-persistence-{suffix}"
        system_marker = f"vforge-upper-{suffix}"
        token = uuid.uuid4().hex
        system_token = uuid.uuid4().hex
        selected_store = pathlib.Path(xdg.name) / "selected-vforge-storage"
        run(["vforge", "config", "storage-root", str(selected_store)])
        denied_name = f"qemu-vforge-denied-{os.getuid()}-{suffix}"
        permission_denied = False
        try:
            run_with_tty(["vforge", "create", denied_name, "--network", "bridge"], "no\n")
        except RuntimeError:
            permission_denied = True
        if not permission_denied or container_exists(f"vforge-{denied_name}", selected_store / "containers"):
            raise RuntimeError("declining host network permission did not prevent environment creation")
        created_name: str | None = None
        created_graphroot: pathlib.Path | None = None
        try:
            size_bytes = 2 * 1024**3
            run_with_tty(["vforge", "create", name, "--network", "bridge", "--size", "2GiB"], "yes\n")
            created_name = name
            metadata = json.loads((vforge_store() / "state/environments.json").read_text())
            environment = metadata["environments"][name]
            runtime_id = vforge_module.host_runtime_id()
            if environment.get("host_runtime") != runtime_id:
                raise RuntimeError("environment metadata is not bound to the active Veyra runtime identity")
            if environment.get("size_bytes") != size_bytes:
                raise RuntimeError("Vforge did not persist the requested environment size cap")
            data_root = pathlib.Path(environment["data_root"])
            graphroot = pathlib.Path(environment["podman_root"])
            created_graphroot = graphroot
            size_image = pathlib.Path(environment["size_image"])
            if data_root != selected_store / "environments" / name / "store":
                raise RuntimeError("Vforge did not use the configured common storage root")
            if graphroot != data_root / "containers" or size_image != data_root.parent / ".vforge-size.img":
                raise RuntimeError("Vforge size cap does not cover its Podman graphroot")
            if not size_image.is_file() or size_image.stat().st_size != size_bytes:
                raise RuntimeError("Vforge backing file is not a sparse file of the requested size")
            statfs = run(["stat", "-f", "--format=%S %b", str(data_root)], capture=True).stdout.split()
            block_size, filesystem_blocks = map(int, statfs)
            filesystem_bytes = block_size * filesystem_blocks
            if filesystem_bytes > size_bytes or filesystem_bytes < size_bytes * 0.99:
                raise RuntimeError(f"size-capped filesystem reports {filesystem_bytes} bytes, expected about {size_bytes}")
            if size_image.stat().st_blocks * 512 >= size_bytes // 2:
                raise RuntimeError("Vforge size-cap backing image appears preallocated")

            # Integrated mode must expose the current Veyra /usr lower layer,
            # not silently substitute the Fedora container's /usr.
            host_abi = pathlib.Path("/usr/share/veyra/sysext-level").read_text().strip()
            container_abi = run(
                ["vforge", "exec", name, "--", "/bin/cat", "/usr/share/veyra/sysext-level"],
                capture=True,
            ).stdout.strip()
            if not host_abi or container_abi != host_abi:
                raise RuntimeError("integrated environment did not read the active Veyra /usr lower layer")

            # A persisted integrated environment must fail closed when its
            # recorded Veyra generation no longer matches the active lower.
            run(["vforge", "stop", name])
            state_path = vforge_module.common_state_directory() / "environments.json"
            environment_state = vforge_module.read_store_state(
                "environments.json", {"version": vforge_module.STATE_VERSION, "environments": {}}
            )
            stored_environment = environment_state["environments"][name]
            try:
                stored_environment["host_runtime"] = "stale-ota-generation"
                vforge_module.write_json_file(state_path, environment_state)
                stale_start = run(["vforge", "start", name], capture=True, check=False)
                stale_error = (stale_start.stdout or "") + (stale_start.stderr or "")
                if stale_start.returncode == 0 or "different Veyra runtime" not in stale_error:
                    raise RuntimeError("Vforge did not block an environment bound to a stale Veyra generation")
            finally:
                stored_environment["host_runtime"] = runtime_id
                vforge_module.write_json_file(state_path, environment_state)
            run(["vforge", "start", name])

            container_release = os_release_fields(run(
                ["vforge", "exec", name, "--", "/bin/cat", "/etc/os-release"], capture=True
            ).stdout)
            if container_release.get("ID") != "fedora" or container_release.get("VERSION_ID") != "44":
                raise RuntimeError(f"Vforge test container must be Fedora 44, found {container_release!r}")
            run(["vforge", "exec", name, "--", "dnf", "-y", "install", "tree"])
            run(["vforge", "exec", name, "--", "rpm", "-q", "tree"])
            layer_id = environment["layer_id"]
            rpmdb_upper = data_root / "layers" / layer_id / "rpmdb-upper"
            if not any(rpmdb_upper.rglob("*")):
                raise RuntimeError("dnf did not persist the Veyra RPM database changes in the private upper")
            if directory_digest(rpmdb_path) != rpmdb_before:
                raise RuntimeError("dnf changed the host Veyra RPM database instead of its per-environment upper")

            # A system path write must land in the per-environment upper only.
            system_path = f"/usr/share/{system_marker}"
            run(["vforge", "exec", name, "--", "/bin/sh", "-ec",
                 f"printf '%s\\n' '{system_token}' > '{system_path}'"])
            if pathlib.Path(system_path).exists():
                raise RuntimeError("integrated environment changed the host's Veyra /usr tree")
            upper_file = data_root / "layers" / layer_id / "usr-upper" / "share" / system_marker
            if upper_file.read_text().strip() != system_token:
                raise RuntimeError("integrated environment did not persist its /usr write in the private upper")

            run(["vforge", "snapshot", name, "system-upper"])
            run(["vforge", "exec", name, "--", "/bin/rm", system_path])
            run(["vforge", "exec", name, "--", "/bin/sh", "-ec", f"test ! -e '{system_path}'"])
            run_with_tty(["vforge", "rollback", name, "system-upper"], "yes\n")
            run(["vforge", "exec", name, "--", "/bin/sh", "-ec",
                 f"test \"$(cat '{system_path}')\" = '{system_token}'"])
            print("Veyra /usr lower, private upper, and snapshot rollback passed.", flush=True)

            # Exercise the interactive entry point and write into the
            # container's writable layer, not the host or a mounted directory.
            shell_command = (
                f"printf '%s\\n' '{token}' > /var/tmp/{marker}; "
                "printf 'VFORGE_ENTER_COMMAND_RAN\\n'"
            )
            enter_output = run_with_tty(["vforge", "enter", name], shell_command + "\nexit\n")
            if "VFORGE_ENTER_COMMAND_RAN" not in enter_output:
                raise RuntimeError("the command entered through vforge enter did not run")

            verify = f"test \"$(cat /var/tmp/{marker})\" = '{token}'"
            run(["vforge", "exec", name, "--", "/bin/sh", "-ec", verify])
            run(["vforge", "stop", name])
            run(["vforge", "start", name])
            run(["vforge", "exec", name, "--", "/bin/sh", "-ec", verify])
            print("Vforge state persisted across stop/start and exec.", flush=True)

            # Exhaust the actual sparse ext4 filesystem, then prove an
            # integrated system write receives ENOSPC. The probe is removed
            # before delete so cleanup itself is not testing full-disk behavior.
            filler = exhaust_store_until_enospc(data_root)
            try:
                probe = f"/usr/share/vforge-enospc-{suffix}"
                probe_command = (
                    f"if dd if=/dev/zero of='{probe}' bs=1M count=64 status=none 2>&1; then "
                    "printf '%s\\n' VFORGE_ENOSPC_UNEXPECTED_SUCCESS; exit 1; "
                    "else status=$?; fi; "
                    "printf '%s\\n' VFORGE_ENOSPC_PROBE_EXECUTED; test \"$status\" -ne 0"
                )
                write_probe = run(
                    ["vforge", "exec", name, "--", "/bin/sh", "-ec", probe_command],
                    capture=True, check=False,
                )
                require_enospc_probe(write_probe)
            finally:
                filler.unlink(missing_ok=True)
            run(["vforge", "exec", name, "--", "/bin/rm", "-f", probe], check=False)
            print("Sparse ext4 size cap reached ENOSPC and recovered after freeing space.", flush=True)

            delete_environment(name)
            created_name = None
            if upper_file.exists():
                raise RuntimeError("vforge delete left its Veyra upper layer behind")
            if size_image.exists() or data_root.parent.exists():
                raise RuntimeError("vforge delete left its size-capped environment store behind")

            # Check the separately stored project environment state and prove
            # the project bind mount cannot read its own generated .vforge data.
            project_name = f"qemu-project-{os.getuid()}-{uuid.uuid4().hex[:8]}"
            with tempfile.TemporaryDirectory(prefix="vforge-project-") as temporary:
                project = pathlib.Path(temporary)
                (project / "source.txt").write_text("project source\n", encoding="utf-8")
                (project / ".vforge.toml").write_text(
                    f'[environment]\nname = "{project_name}"\nisolation = "integrated"\n',
                    encoding="utf-8",
                )
                run(["git", "init", "--quiet", str(project)])
                try:
                    run_with_tty(["vforge", "up"], "yes\n", cwd=project)
                    if not (project / ".vforge/environment.json").is_file():
                        raise RuntimeError("project Vforge state was not stored in the project's .vforge directory")
                    run(
                        ["vforge", "exec", project_name, "--", "/bin/sh", "-ec",
                         "test -f /workspace/source.txt && test ! -e /workspace/.vforge/environment.json"]
                    )
                    ignored = run(
                        ["git", "-C", str(project), "check-ignore", ".vforge/environment.json"], capture=True
                    ).stdout.strip()
                    if ignored != ".vforge/environment.json":
                        raise RuntimeError("project-local Vforge state is not excluded from Git")
                    delete_environment(project_name)
                finally:
                    project_graphroot = project / ".vforge/containers"
                    if (project / ".vforge/environment.json").exists():
                        try:
                            delete_environment(project_name)
                        except (OSError, RuntimeError, subprocess.CalledProcessError):
                            print(f"Could not clean up project environment {project_name}.", file=sys.stderr, flush=True)
                    elif container_exists(f"vforge-{project_name}", project_graphroot):
                        run(["podman", "--remote=false", *podman_roots(project_graphroot), "container", "rm", "--force", f"vforge-{project_name}"], check=False)
                if (project / ".vforge").exists() or not (project / "source.txt").exists():
                    raise RuntimeError("project environment deletion did not preserve source and remove only .vforge state")
            print("Vforge rootless create/enter/exec/stop/start/delete passed in QEMU.", flush=True)
            return 0
        finally:
            if created_name and container_exists(f"vforge-{created_name}", created_graphroot):
                print(f"Cleaning up test environment {created_name}.", file=sys.stderr, flush=True)
                delete_environment(created_name)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"vforge QEMU integration test: {error}", file=sys.stderr)
        return 1
    finally:
        for key, value in old_xdg.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        xdg.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
