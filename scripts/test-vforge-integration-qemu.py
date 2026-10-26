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
        raise RuntimeError("could not verify the QEMU guest") from error
    if virtualizer not in {"qemu", "kvm"}:
        raise RuntimeError(f"Veyra integration tests must run inside QEMU, detected {virtualizer!r}")
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
    created: str | None = None
    created_graphroot: pathlib.Path | None = None
    try:
        require_qemu_guest()
        os.environ["XDG_DATA_HOME"] = str(pathlib.Path(xdg.name) / "data")
        os.environ["XDG_CONFIG_HOME"] = str(pathlib.Path(xdg.name) / "config")
        os.environ["XDG_STATE_HOME"] = str(pathlib.Path(xdg.name) / "state")
        archive_text = os.environ.get("VFORGE_BASE_ARCHIVE")
        if not archive_text:
            raise RuntimeError("the QEMU runner must provide VFORGE_BASE_ARCHIVE from the matching OCI image build")
        archive = pathlib.Path(archive_text)
        if not archive.is_file() or archive.stat().st_size == 0:
            raise RuntimeError(f"Veyra OCI base archive is missing or empty: {archive}")
        if not shutil.which("vforge") or not shutil.which("podman"):
            raise RuntimeError("the Veyra guest must contain both vforge and Podman")
        vforge_module = load_vforge_module()
        rootless = run(
            ["podman", "--remote=false", "info", "--format", "{{.Host.Security.Rootless}}"],
            capture=True,
        ).stdout.strip()
        if rootless.lower() != "true":
            raise RuntimeError(f"Podman is not using its rootless store (reported {rootless!r})")

        base_before = os_release_fields(pathlib.Path("/etc/os-release").read_text(encoding="utf-8"))
        host_rpmdb = pathlib.Path(run(["rpm", "--eval", "%{_dbpath}"], capture=True).stdout.strip())
        host_rpmdb_before = directory_digest(host_rpmdb)
        run(["vforge", "base", "import", str(archive)])
        run(["vforge", "base", "status"])
        base_identity = vforge_module.require_imported_veyra_base()

        suffix = uuid.uuid4().hex[:10]
        name = f"qemu-vforge-{os.getuid()}-{suffix}"
        token = uuid.uuid4().hex
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

        try:
            run_with_tty(["vforge", "create", name, "--network", "bridge"], "yes\n")
            created = name
            environment = vforge_module.env_state()[name]
            created_graphroot = pathlib.Path(environment["podman_root"])
            if environment.get("root_mode") != "veyra-oci":
                raise RuntimeError("Vforge did not record a Veyra OCI root")
            if (
                environment.get("image_id") != base_identity["image_id"]
                or environment.get("image_version") != base_identity["image_version"]
                or environment.get("base_digest") != base_identity["base_digest"]
            ):
                raise RuntimeError("Vforge environment does not match the imported Veyra base identity")
            root_id = environment.get("root_id")
            if not isinstance(root_id, str):
                raise RuntimeError("Vforge environment has no root_id")
            marker = pathlib.Path(environment["data_root"]) / "container-root.json"
            vforge_module.validate_container_root_marker(environment)
            container = vforge_module.inspect(f"vforge-{name}")
            vforge_module.validate_container_identity(container, environment)
            vforge_module.validate_container_mounts(container)
            marker_mount = next(
                mount for mount in container["Mounts"]
                if mount.get("Destination") == "/run/veyra-vforge/root.json"
            )
            if marker_mount.get("RW") is not False:
                raise RuntimeError("Vforge root marker is not mounted read-only")
            for mount in container["Mounts"]:
                destination = mount.get("Destination", "")
                if destination in {"/etc", "/usr", "/var", "/boot"} or any(
                    destination.startswith(path + "/") for path in ("/etc", "/usr", "/var", "/boot")
                ):
                    raise RuntimeError(f"host mount overlays the private Veyra root: {destination}")
            marker_data = json.loads(marker.read_text(encoding="utf-8"))
            if marker_data.get("root_id") != root_id or marker_data.get("base_digest") != environment["base_digest"]:
                raise RuntimeError("read-only marker does not describe this environment's Veyra root")

            container_release = os_release_fields(run(
                ["vforge", "exec", name, "--", "/bin/cat", "/etc/os-release"], capture=True
            ).stdout)
            if container_release.get("ID") != base_before.get("ID") or container_release.get("VERSION_ID") != "44":
                raise RuntimeError(f"environment did not start from the Veyra OCI root: {container_release!r}")
            run(["vforge", "exec", name, "--", "/bin/sh", "-ec",
                 'test -d /usr/lib/sysimage/rpm && test -x /usr/bin/vpkg && test "$(id -u)" = 0 && '
                 'test -r /run/veyra-vforge/root.json && test "$(rpm --eval \'%{_dbpath}\')" = /usr/lib/sysimage/rpm'])
            if directory_digest(host_rpmdb) != host_rpmdb_before:
                raise RuntimeError("base import or container creation changed the host RPM database")

            package_before = run(
                ["vforge", "exec", name, "--", "rpm", "-q", "tree"], capture=True, check=False
            )
            if package_before.returncode == 0:
                raise RuntimeError("test package tree is already present in the imported Veyra root")
            run(["vforge", "exec", name, "--", "vpkg", "container", "install", "tree"])
            run(["vforge", "exec", name, "--", "rpm", "-q", "tree"])
            if directory_digest(host_rpmdb) != host_rpmdb_before:
                raise RuntimeError("vpkg container install changed the host RPM database")

            marker_path = f"/var/tmp/vforge-snapshot-{suffix}"
            run(["vforge", "exec", name, "--", "/bin/sh", "-ec",
                 f"printf '%s\n' '{token}' > '{marker_path}'"])
            run(["vforge", "snapshot", name, "vpkg-root"])
            run(["vforge", "exec", name, "--", "vpkg", "container", "remove", "tree"])
            removed = run(["vforge", "exec", name, "--", "rpm", "-q", "tree"], capture=True, check=False)
            if removed.returncode == 0:
                raise RuntimeError("vpkg container remove did not remove tree from the environment RPM DB")
            run(["vforge", "exec", name, "--", "/bin/rm", marker_path])
            run_with_tty(["vforge", "rollback", name, "vpkg-root"], "yes\n")
            run(["vforge", "exec", name, "--", "rpm", "-q", "tree"])
            run(["vforge", "exec", name, "--", "/bin/sh", "-ec",
                 f"test \"$(cat '{marker_path}')\" = '{token}'"])
            if environment.get("root_id") != root_id or marker_data.get("root_id") != root_id:
                raise RuntimeError("snapshot rollback changed the environment root_id")
            if directory_digest(host_rpmdb) != host_rpmdb_before:
                raise RuntimeError("vpkg transaction or snapshot rollback changed the host RPM database")
            print("Veyra OCI root, private RPM DB, Vpkg install, and full-root snapshot rollback passed.", flush=True)

            run(["vforge", "stop", name])
            run(["vforge", "start", name])
            run(["vforge", "exec", name, "--", "rpm", "-q", "tree"])
            delete_environment(name)
            created = None

            project_name = f"qemu-project-{os.getuid()}-{uuid.uuid4().hex[:8]}"
            with tempfile.TemporaryDirectory(prefix="vforge-project-") as temporary:
                project = pathlib.Path(temporary)
                (project / "source.txt").write_text("project source\n", encoding="utf-8")
                (project / ".vforge.toml").write_text(
                    f'[environment]\nname = "{project_name}"\nisolation = "integrated"\n\n'
                    '[packages]\nsystem = ["tree"]\n\n[permissions]\nnetwork = "shared"\n',
                    encoding="utf-8",
                )
                run(["git", "init", "--quiet", str(project)])
                try:
                    run_with_tty(["vforge", "up"], "yes\n", cwd=project)
                    run(["vforge", "exec", project_name, "--", "rpm", "-q", "tree"])
                    run(["vforge", "exec", project_name, "--", "/bin/sh", "-ec",
                         "test -f /workspace/source.txt && test ! -e /workspace/.vforge/environment.json"])
                    ignored = run(
                        ["git", "-C", str(project), "check-ignore", ".vforge/environment.json"], capture=True
                    ).stdout.strip()
                    if ignored != ".vforge/environment.json":
                        raise RuntimeError("project-local Vforge state is not excluded from Git")
                    delete_environment(project_name)
                    if (project / ".vforge").exists() or not (project / "source.txt").exists():
                        raise RuntimeError("project environment deletion did not preserve source and remove only .vforge state")
                finally:
                    if (project / ".vforge/environment.json").exists():
                        try:
                            delete_environment(project_name)
                        except (OSError, RuntimeError, subprocess.CalledProcessError):
                            print(f"Could not clean up project environment {project_name}.", file=sys.stderr, flush=True)
            print("Vforge rootless create/enter/exec/stop/start/delete passed in QEMU.", flush=True)
            return 0
        finally:
            if created and container_exists(f"vforge-{created}", created_graphroot):
                print(f"Cleaning up test environment {created}.", file=sys.stderr, flush=True)
                delete_environment(created)
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
