#!/usr/bin/env python3
"""Real Fedora 44 RPM transaction using vpkg's own libdnf5 adapter.

Runs in a disposable Fedora CI container, never on the host OS or user disk.
This tests RPM repository fetching, dependency resolution and installation;
it does not replace an actual booted Live ISO + systemd-sysext QEMU test.
"""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mkosi.extra/usr/lib/veyra"))
from vpkg_libdnf import Session

with tempfile.TemporaryDirectory(prefix="veyra-vpkg-real-rpm-") as temporary:
    root = Path(temporary)
    session = Session(
        installroot=root / "installroot",
        releasever="44",
        repo_ids=("fedora", "updates"),
        cache_dir=root / "cache",
        history_dir=root / "history",
        persist_dir=root / "persist",
        state_dir=root / "state",
    )
    result = session.resolve("install", ("tree",))
    package = root / "installroot/usr/bin/tree"
    if not package.is_file():
        raise SystemExit("FAIL: tree RPM transaction did not create /usr/bin/tree")
    if not any(change.name == "tree" for change in result.changes):
        raise SystemExit("FAIL: missing tree package transaction record")
    print("VEYRA_VPKG_REAL_RPM_PASS package=tree", flush=True)
