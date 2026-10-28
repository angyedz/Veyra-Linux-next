#!/usr/bin/env python3
"""End-to-end Live ISO -> real installer -> installed Veyra UEFI boot in QEMU.

The ONLY target is a newly created disposable qcow2 regular file. A separate
virtio-block serial and a live-guest QEMU identity guard are required before the
guest is allowed to invoke the destructive installer. Host block devices are
never opened, passed to QEMU or used as installer targets. Never print creds.
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

TARGET_SERIAL = "VEYRA_CI_INSTALL"
TARGET_DEVICE = "/dev/vda"
DISK_BYTES = 64 * 1024**3
PASSWORD_LENGTH = 24


class InstallQemuError(RuntimeError):
    pass


def qemu_command(*, disk: pathlib.Path, serial: pathlib.Path, agent: pathlib.Path,
                 code: pathlib.Path, vars_file: pathlib.Path,
                 iso: pathlib.Path | None) -> list[str]:
    # Never accept a block device as QEMU media. Everything is a regular file.
    if not disk.is_file() or disk.is_symlink() or disk.suffix != ".qcow2":
        raise InstallQemuError("installer target must be a freshly created regular qcow2 file")
    if iso is not None and (not iso.is_file() or iso.is_symlink()):
        raise InstallQemuError("Live ISO must be a regular file")
    if not code.is_file() or not vars_file.is_file():
        raise InstallQemuError("OVMF firmware is unavailable")
    cmd = [
        "qemu-system-x86_64", "-machine", "q35,accel=tcg",
        "-cpu", "max", "-smp", "2", "-m", "5120",
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={vars_file}",
        "-drive", f"if=none,id=ci-target,format=qcow2,file={disk}",
        "-device", f"virtio-blk-pci,drive=ci-target,serial={TARGET_SERIAL}",
        "-device", "virtio-serial-pci",
        "-chardev", f"socket,path={agent},server=on,wait=off,id=qga0",
        "-device", "virtserialport,chardev=qga0,name=org.qemu.guest_agent.0",
        "-vga", "none", "-device", "virtio-vga", "-display", "none",
        "-nic", "user", "-no-reboot",
        "-serial", f"file:{serial}",
        "-monitor", "none",
        "-boot", f"order={'d' if iso is not None else 'c'},menu=off",
    ]
    if iso is not None:
        cmd.extend(["-drive", f"file={iso},if=ide,media=cdrom,format=raw,readonly=on"])
    return cmd


def guest_install_shell() -> str:
    # This shell runs exclusively *inside* the guest. The exact virtio disk
    # serial and size are checked before even an installer preflight.
    return """set -euo pipefail
# Never expose credentials in a guest process argument or CI capture output.
# All installer output stays on the disposable guest tmpfs for diagnostics.
exec >/run/veyra-ci-install.log 2>&1
test "$(id -u)" -eq 0
case "$(systemd-detect-virt --vm 2>/dev/null)" in qemu|kvm) ;; *) exit 80 ;; esac
test "$(lsblk -dn -o TYPE /dev/vda | tr -d '[:space:]')" = disk
test "$(lsblk -dn -o SERIAL /dev/vda | tr -d '[:space:]')" = VEYRA_CI_INSTALL
test "$(blockdev --getsize64 /dev/vda)" -eq 68719476736
test -s /usr/share/veyra/installer/source/assets/veyra-recovery.efi
/usr/libexec/veyra-installer --preflight --target /dev/vda
/usr/libexec/veyra-installer --target /dev/vda --confirm-device /dev/vda
echo VEYRA_REAL_INSTALLER_QEMU_PASS
"""


def firstboot_shell() -> str:
    return """set -euo pipefail
grep -Fxq ID=fedora /usr/lib/os-release
grep -Fxq VERSION_ID=44 /usr/lib/os-release
grep -Fxq 'IMAGE_ID="veyra-next"' /usr/lib/os-release
grep -Eq 'roothash=[0-9a-f]{64}' /proc/cmdline
test "$(findmnt -no FSTYPE /)" = erofs
findmnt -no OPTIONS / | grep -Eq '(^|,)ro(,|$)'
test "$(findmnt -no FSTYPE /home)" = ext4
systemctl is-active --quiet graphical.target
systemctl is-active --quiet display-manager.service
test -x /usr/bin/vpkg
test -x /usr/bin/qemu-ga
echo VEYRA_INSTALLED_UEFI_QEMU_PASS
"""


def guest_credentials() -> bytes:
    # Never write these credentials to the host disk, command line or CI logs.
    user = secrets.token_urlsafe(PASSWORD_LENGTH)
    root = secrets.token_urlsafe(PASSWORD_LENGTH)
    return ("veyra-ci\n" + user + "\n" + user + "\n" + root + "\n" + root + "\n").encode()


def agent_call(sock: pathlib.Path, action: str, arguments: dict | None = None) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(8)
        client.connect(str(sock))
        # QGA recommends sync before each command; discard prior responses.
        token = 68324
        client.sendall((json.dumps({"execute": "guest-sync", "arguments": {"id": token}}) + "\n").encode())
        buffer = b""
        end = time.monotonic() + 8
        while time.monotonic() < end:
            buffer += client.recv(65536)
            lines = buffer.split(b"\n")
            buffer = lines.pop()
            if any(json.loads(line).get("return") == token for line in lines if line.strip()):
                break
        else:
            raise InstallQemuError("QGA sync did not respond")
        request = {"execute": action}
        if arguments is not None:
            request["arguments"] = arguments
        client.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            buffer += client.recv(65536)
            if b"\n" not in buffer:
                continue
            line, _unused = buffer.split(b"\n", 1)
            answer = json.loads(line)
            if "error" in answer:
                raise InstallQemuError(f"QGA {action} rejected request: {answer['error']}")
            if "return" not in answer:
                raise InstallQemuError(f"QGA {action} returned no result")
            return answer["return"]
    raise InstallQemuError(f"QGA {action} timed out")


def wait_agent(path: pathlib.Path, process: subprocess.Popen, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise InstallQemuError(f"QEMU exited before guest agent started ({process.returncode})")
        if path.is_socket():
            try:
                agent_call(path, "guest-ping")
                return
            except (OSError, ValueError, InstallQemuError):
                pass
        time.sleep(3)
    raise InstallQemuError("QEMU guest agent did not start on the Live/installed system")


def run_guest_command(agent: pathlib.Path, script: str, stdin: bytes | None = None) -> int:
    arguments: dict = {
        "path": "/usr/bin/bash",
        "arg": ["-ceu", script],
        "capture-output": True,
    }
    if stdin is not None:
        arguments["input-data"] = base64.b64encode(stdin).decode("ascii")
    reply = agent_call(agent, "guest-exec", arguments)
    pid = reply.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        raise InstallQemuError("guest-exec did not return a valid PID")
    return pid


def wait_guest_exit(agent: pathlib.Path, pid: int, timeout: int) -> tuple[int, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = agent_call(agent, "guest-exec-status", {"pid": pid})
        if state.get("exited") is True:
            code = state.get("exitcode", 1)
            output = b""
            for key in ("out-data", "err-data"):
                if state.get(key):
                    output += base64.b64decode(state[key])
            return int(code), output.decode("utf-8", errors="replace")[-12000:]
        time.sleep(4)
    raise InstallQemuError(f"guest command {pid} timed out after {timeout} seconds")


def stop_vm(vm: subprocess.Popen) -> None:
    if vm.poll() is None:
        vm.terminate()
        try:
            vm.wait(timeout=12)
        except subprocess.TimeoutExpired:
            vm.kill()
            vm.wait(timeout=12)


def log_excerpt(path: pathlib.Path) -> str:
    if not path.is_file():
        return "<serial log missing>"
    return path.read_text(encoding="utf-8", errors="replace")[-12000:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", type=pathlib.Path, required=True)
    parser.add_argument("--ovmf-code", type=pathlib.Path, required=True)
    parser.add_argument("--ovmf-vars", type=pathlib.Path, required=True)
    parser.add_argument("--report-dir", type=pathlib.Path, required=True)
    parser.add_argument("--boot-timeout", type=int, default=720)
    parser.add_argument("--install-timeout", type=int, default=7800)
    args = parser.parse_args()
    report = args.report_dir.resolve()
    report.mkdir(parents=True, exist_ok=True)
    results = {"schema": 1, "installer_completed": False, "installed_firstboot": False}
    try:
        for executable in ("qemu-img", "qemu-system-x86_64"):
            if not shutil.which(executable):
                raise InstallQemuError(f"required emulator missing: {executable}")
        iso = args.iso.resolve(strict=True)
        if not iso.is_file() or not 0 < iso.stat().st_size <= 8 * 1024**3:
            raise InstallQemuError("Live ISO missing or violates 8 GiB limit")
        with tempfile.TemporaryDirectory(prefix="veyra-disposable-install-") as workdir:
            work = pathlib.Path(workdir)
            disk = work / "installer-target.qcow2"
            subprocess.run(["qemu-img", "create", "-f", "qcow2", str(disk), str(DISK_BYTES)],
                           check=True, capture_output=True)
            # OVMF variables must NEVER be shared across the two boots or any host.
            code = args.ovmf_code.resolve(strict=True)
            template = args.ovmf_vars.resolve(strict=True)
            variables = work / "OVMF_VARS.fd"
            shutil.copyfile(template, variables)
            for phase in ("live-install", "installed-firstboot"):
                serial = report / f"{phase}.serial.log"
                agent = work / f"{phase}.qga.sock"
                command = qemu_command(
                    disk=disk, serial=serial, agent=agent, code=code, vars_file=variables,
                    iso=iso if phase == "live-install" else None,
                )
                with (report / f"{phase}.qemu.stderr").open("wb") as error_log:
                    vm = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=error_log)
                    try:
                        wait_agent(agent, vm, args.boot_timeout)
                        if phase == "live-install":
                            credentials = guest_credentials()
                            try:
                                pid = run_guest_command(
                                    agent, guest_install_shell(), stdin=credentials,
                                )
                            finally:
                                del credentials
                            code_status, output = wait_guest_exit(agent, pid, args.install_timeout)
                            (report / "installer-exit.json").write_text(
                                json.dumps({"exitcode": code_status}, indent=2) + "\n"
                            )
                            try:
                                tail_pid = run_guest_command(
                                    agent, "tail -c 20000 /run/veyra-ci-install.log"
                                )
                                tail_status, tail_text = wait_guest_exit(agent, tail_pid, 60)
                                if tail_status == 0:
                                    (report / "installer-tail.log").write_text(tail_text)
                            except (OSError, ValueError, InstallQemuError):
                                tail_text = "<could not read installer log from guest>"
                            if code_status or "VEYRA_REAL_INSTALLER_QEMU_PASS" not in tail_text:
                                raise InstallQemuError(
                                    f"guest installer failed or success marker absent (code={code_status}): "
                                    + tail_text[-5000:]
                                )
                            results["installer_completed"] = True
                        else:
                            pid = run_guest_command(agent, firstboot_shell())
                            code_status, output = wait_guest_exit(agent, pid, 240)
                            (report / "firstboot-check.txt").write_text(output)
                            if code_status or "VEYRA_INSTALLED_UEFI_QEMU_PASS" not in output:
                                raise InstallQemuError(
                                    f"installed first-boot gate failed (code={code_status}): {output}"
                                )
                            results["installed_firstboot"] = True
                            print("VEYRA_REAL_INSTALL_AND_FIRSTBOOT_QEMU_PASS", flush=True)
                    finally:
                        stop_vm(vm)
    except (OSError, ValueError, subprocess.CalledProcessError, InstallQemuError) as error:
        results["error"] = str(error)
        print(f"Veyra real installation QEMU test failed: {error}", file=sys.stderr)
        for phase in ("live-install", "installed-firstboot"):
            print(f"--- {phase} serial ---\n{log_excerpt(report / f'{phase}.serial.log')}",
                  file=sys.stderr)
    finally:
        (report / "report.json").write_text(json.dumps(results, indent=2) + "\n")
    return 0 if results["installer_completed"] and results["installed_firstboot"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
