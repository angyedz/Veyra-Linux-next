#!/usr/bin/env bash
set -euo pipefail

# Pin the image builder so a later upstream release cannot silently change
# this repository's build behavior.
if [[ -e /etc/fedora-release ]] && command -v dnf5 >/dev/null 2>&1; then
    host_release="$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '\"')"
    if [[ "$host_release" != 44 ]]; then
        echo "Build Veyra from a clean Fedora 44 environment; found Fedora $host_release." >&2
        exit 1
    fi
    allowed_repositories=$'fedora\nupdates\nfedora-cisco-openh264\nrpmfusion-free\nrpmfusion-free-updates\nrpmfusion-nonfree\nrpmfusion-nonfree-updates\nveyra-main\nveyra-testing'
    while IFS= read -r repository; do
        [[ -z "$repository" ]] && continue
        if ! grep -Fxq -- "$repository" <<<"$allowed_repositories"; then
            echo "Refusing a non-hermetic Fedora build: host repository '$repository' is enabled." >&2
            echo "Use a clean Fedora 44 build container with only Fedora 44, RPM Fusion, and Veyra repositories." >&2
            exit 1
        fi
    done < <(dnf5 repo list --enabled | awk 'NR > 1 { print $1 }')
fi

exec uv tool run \
    --from git+https://github.com/systemd/mkosi@47560954a66c0a992e1a6ef9711177a8fc3ec5e6 \
    mkosi "$@"
