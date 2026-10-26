#!/usr/bin/python3
"""Provide disposable host sockets and prove Vforge can connect through them."""

from __future__ import annotations

import os
from pathlib import Path
import socket
import sys


SOCKETS = (
    ("wayland", "wayland-0"),
    ("pipewire", "pipewire-0"),
    ("pulse", "pulse/native"),
)


def serve(runtime: Path, ready: Path) -> None:
    listeners: list[tuple[str, socket.socket]] = []
    try:
        for name, relative_path in SOCKETS:
            path = runtime / relative_path
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.unlink(missing_ok=True)
            endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            endpoint.bind(str(path))
            endpoint.listen(1)
            endpoint.settimeout(45)
            listeners.append((name, endpoint))
        ready.write_text("ready\n", encoding="utf-8")
        for name, endpoint in listeners:
            connection, _ = endpoint.accept()
            with connection:
                connection.sendall(f"veyra-vforge-{name}-connected".encode())
        ready.write_text("complete\n", encoding="utf-8")
    finally:
        for _, endpoint in listeners:
            endpoint.close()


def connect_from_environment() -> None:
    runtime = Path(os.environ["XDG_RUNTIME_DIR"])
    if os.environ.get("WAYLAND_DISPLAY") != "wayland-0":
        raise RuntimeError("Vforge did not pass the approved WAYLAND_DISPLAY")
    if os.environ.get("PIPEWIRE_REMOTE") != "pipewire-0":
        raise RuntimeError("Vforge did not pass the approved PIPEWIRE_REMOTE")
    pulse_server = os.environ.get("PULSE_SERVER", "")
    if pulse_server != f"unix:{runtime}/pulse/native":
        raise RuntimeError("Vforge did not pass the approved PULSE_SERVER")

    endpoints = {
        "wayland": runtime / os.environ["WAYLAND_DISPLAY"],
        "pipewire": runtime / os.environ["PIPEWIRE_REMOTE"],
        "pulse": Path(pulse_server.removeprefix("unix:")),
    }
    for name, path in endpoints.items():
        if not path.is_socket():
            raise RuntimeError(f"Vforge did not mount the approved {name} socket at {path}")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(10)
            client.connect(str(path))
            response = client.recv(128).decode("ascii")
        if response != f"veyra-vforge-{name}-connected":
            raise RuntimeError(f"Vforge reached the wrong {name} socket: {response!r}")
        print(f"Vforge connected to the approved host {name} socket.", flush=True)


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("usage: vforge-desktop-sockets-ci.py serve RUNTIME READY | client")
    if sys.argv[1] == "serve" and len(sys.argv) == 4:
        serve(Path(sys.argv[2]), Path(sys.argv[3]))
    elif sys.argv[1] == "client" and len(sys.argv) == 2:
        connect_from_environment()
    else:
        raise SystemExit("invalid vforge desktop socket CI arguments")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
