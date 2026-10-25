#!/usr/bin/python3
"""Exercise a real rootless Vforge lifecycle inside a disposable Veyra VM."""

from __future__ import annotations

import os
import pathlib
import pty
import select
import shutil
import subprocess
import sys
import time
import uuid


IMAGE = "registry.fedoraproject.org/fedora:44"


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


def run(command: list[str], *, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def run_with_tty(command: list[str], input_text: str, *, timeout: int = 45) -> str:
    master, slave = pty.openpty()
    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave, close_fds=True)
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


def container_exists(name: str) -> bool:
    result = run(["podman", "--remote=false", "container", "exists", name], check=False)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise RuntimeError(f"could not inspect Podman container {name} (status {result.returncode})")


def delete_environment(name: str) -> None:
    run_with_tty(["vforge", "delete", name], "yes\n")


def main() -> int:
    try:
        require_qemu_guest()
        if not shutil.which("vforge") or not shutil.which("podman"):
            raise RuntimeError("the Veyra guest must contain both vforge and Podman")

        rootless = run(
            ["podman", "--remote=false", "info", "--format", "{{.Host.Security.Rootless}}"],
            capture=True,
        ).stdout.strip()
        if rootless.lower() != "true":
            raise RuntimeError(f"Podman is not using its rootless store (reported {rootless!r})")

        image_state = run(
            ["podman", "--remote=false", "image", "exists", IMAGE], check=False
        ).returncode
        if image_state == 1:
            print(f"Pulling {IMAGE} inside the QEMU guest.", flush=True)
            run(["podman", "--remote=false", "pull", IMAGE])
        elif image_state != 0:
            raise RuntimeError(f"could not inspect the guest's Podman image store (status {image_state})")

        suffix = uuid.uuid4().hex[:10]
        name = f"qemu-vforge-{os.getuid()}-{suffix}"
        marker = f"vforge-persistence-{suffix}"
        token = uuid.uuid4().hex
        created_name: str | None = None
        try:
            run(["vforge", "create", name])
            created_name = name

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
            if container_exists(f"vforge-{name}"):
                raise RuntimeError("vforge delete left its Podman container behind")
            print("Vforge rootless create/enter/exec/stop/start/delete passed in QEMU.", flush=True)
            return 0
        finally:
            if created_name and container_exists(f"vforge-{created_name}"):
                print(f"Cleaning up test environment {created_name}.", file=sys.stderr, flush=True)
                delete_environment(created_name)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"vforge QEMU integration test: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
