#!/usr/bin/env python3
"""Check the GPT layout of a built Veyra A/B image."""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ROOT_TYPE = "4f68bce3-e8cd-4db1-96e7-fbcaf984b709"
ROOT_VERITY_TYPE = "2c7357ed-ebd2-46d9-aec1-23d437ec2bf5"


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: check-image.py IMAGE.raw", file=sys.stderr)
        return 2

    image = pathlib.Path(sys.argv[1])
    if not image.is_file() or image.stat().st_size == 0:
        print(f"missing disk image: {image}", file=sys.stderr)
        return 1

    try:
        layout = json.loads(subprocess.check_output(["sfdisk", "--json", str(image)], text=True))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        print(f"could not read GPT layout from {image}: {error}", file=sys.stderr)
        return 1

    partitions = layout.get("partitiontable", {}).get("partitions", [])
    root_partitions = [p for p in partitions if p.get("type", "").lower() == ROOT_TYPE]
    verity_partitions = [p for p in partitions if p.get("type", "").lower() == ROOT_VERITY_TYPE]
    labels = {p.get("name", "") for p in partitions}
    osrelease_file = image.with_name(f"{image.stem}.osrelease")
    if not osrelease_file.is_file():
        print(f"missing mkosi os-release artifact: {osrelease_file}", file=sys.stderr)
        return 1
    image_id = ""
    image_version = ""
    for line in osrelease_file.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"IMAGE_ID", "IMAGE_VERSION"}:
            value = value.strip().strip('"')
            if key == "IMAGE_ID":
                image_id = value
            else:
                image_version = value
    if image_id != "veyra-next" or not image_version:
        print(f"unexpected image identity in {osrelease_file}", file=sys.stderr)
        return 1

    active_root_label = f"{image_id}_{image_version}"
    active_verity_label = f"{active_root_label}_verity"
    expected_labels = {active_root_label, active_verity_label, "VEYRA_ETC", "VEYRA_VAR"}

    if len(root_partitions) != 2:
        print(f"expected two root partitions for A/B, found {len(root_partitions)}", file=sys.stderr)
        return 1
    if len(verity_partitions) != 2:
        print(f"expected two root verity partitions for A/B, found {len(verity_partitions)}", file=sys.stderr)
        return 1
    if not expected_labels.issubset(labels):
        missing = ", ".join(sorted(expected_labels - labels))
        print(f"missing GPT partition labels: {missing}", file=sys.stderr)
        return 1

    active_roots = [p for p in root_partitions if p.get("name") == active_root_label]
    active_verities = [p for p in verity_partitions if p.get("name") == active_verity_label]
    if len(active_roots) != 1 or len(active_verities) != 1:
        print("expected exactly one active root/verity pair", file=sys.stderr)
        return 1
    empty_roots = [p for p in root_partitions if p.get("name") == "_empty"]
    empty_verities = [p for p in verity_partitions if p.get("name") == "_empty"]
    if len(empty_roots) != 1 or len(empty_verities) != 1:
        print("expected exactly one empty root slot and one empty verity slot", file=sys.stderr)
        return 1

    root = active_roots[0]
    verity = active_verities[0]
    roothash_file = image.with_name("veyra-next.roothash")
    if not roothash_file.is_file():
        print(f"missing mkosi root hash artifact: {roothash_file}", file=sys.stderr)
        return 1

    encoded_roothash = (root["uuid"].replace("-", "") + verity["uuid"].replace("-", "")).lower()
    roothash = roothash_file.read_text(encoding="ascii").strip().lower()
    if encoded_roothash != roothash:
        print("active root/verity partition UUIDs do not encode the generated dm-verity root hash", file=sys.stderr)
        return 1

    print(f"Veyra A/B image layout is present in {image}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
