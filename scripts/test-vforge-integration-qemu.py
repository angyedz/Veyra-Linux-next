#!/usr/bin/python3
"""Exercise a real rootless Vforge lifecycle inside a disposable Veyra VM."""

from __future__ import annotations

import os
import pathlib
import pty
import select
import shutil
import json
import hashlib
import subprocess
import sys
import tempfile
import time
import uuid


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
    data_home = pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local/share"))
    return (data_home / "vforge").resolve(strict=False)


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
        created_name: str | None = None
        try:
            run_with_tty(["vforge", "create", name, "--network", "bridge"], "yes\n")
            created_name = name

            # Integrated mode must expose the current Veyra /usr lower layer,
            # not silently substitute the Fedora container's /usr.
            host_abi = pathlib.Path("/usr/share/veyra/sysext-level").read_text().strip()
            container_abi = run(
                ["vforge", "exec", name, "--", "/bin/cat", "/usr/share/veyra/sysext-level"],
                capture=True,
            ).stdout.strip()
            if not host_abi or container_abi != host_abi:
                raise RuntimeError("integrated environment did not read the active Veyra /usr lower layer")
            container_release = os_release_fields(run(
                ["vforge", "exec", name, "--", "/bin/cat", "/etc/os-release"], capture=True
            ).stdout)
            if container_release.get("ID") != "fedora" or container_release.get("VERSION_ID") != "44":
                raise RuntimeError(f"Vforge test container must be Fedora 44, found {container_release!r}")
            run(["vforge", "exec", name, "--", "dnf", "-y", "install", "tree"])
            run(["vforge", "exec", name, "--", "rpm", "-q", "tree"])
            metadata = json.loads((vforge_store() / "state/environments.json").read_text())
            layer_id = metadata["environments"][name]["layer_id"]
            rpmdb_upper = vforge_store() / "environments" / name / "layers" / layer_id / "rpmdb-upper"
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
            upper_file = vforge_store() / "environments" / name / "layers" / layer_id / "usr-upper" / "share" / system_marker
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

            delete_environment(name)
            created_name = None
            if upper_file.exists():
                raise RuntimeError("vforge delete left its Veyra upper layer behind")
            if container_exists(f"vforge-{name}"):
                raise RuntimeError("vforge delete left its Podman container behind")

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
            if created_name and container_exists(f"vforge-{created_name}"):
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
