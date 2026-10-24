#!/usr/bin/env bash
set -euo pipefail

# Pin the image builder so a later upstream release cannot silently change
# this repository's build behavior.
exec uv tool run \
    --from git+https://github.com/systemd/mkosi@47560954a66c0a992e1a6ef9711177a8fc3ec5e6 \
    mkosi "$@"
