#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "Usage: $0 <main|testing> <built-rpm-directory> <pages-site-directory>" >&2
    exit 2
fi

channel="$1"
rpm_directory="$2"
site_directory="$3"

case "$channel" in
    main|testing) ;;
    *) echo "Invalid package channel: $channel" >&2; exit 2 ;;
esac

if ! command -v createrepo_c >/dev/null 2>&1; then
    echo "createrepo_c is required to build RPM repository metadata." >&2
    exit 1
fi

repo_directory="$site_directory/packages/$channel/x86_64"
rm -rf -- "$repo_directory"
mkdir -p "$repo_directory"

mapfile -t packages < <(sed -e '/^[[:space:]]*#/d' -e '/^[[:space:]]*$/d' "packages/$channel/packages.list")
for name in "${packages[@]}"; do
    if [[ ! "$name" =~ ^veyra-[A-Za-z0-9][A-Za-z0-9+_.-]*$ ]]; then
        echo "Refusing non-Veyra package listed in $channel: $name" >&2
        exit 1
    fi
    spec="packages/extensions/$name.spec"
    if [[ ! -f "$spec" ]]; then
        echo "Missing package spec: $spec" >&2
        exit 1
    fi
done

if ((${#packages[@]} == 0)); then
    python3 scripts/check-extension-rpms.py "$rpm_directory" --allow-empty
else
    python3 scripts/check-extension-rpms.py "$rpm_directory"
fi

shopt -s nullglob
for rpm in "$rpm_directory"/*.rpm; do
    package_name="$(rpm -qp --qf '%{NAME}' "$rpm")"
    if [[ ! "$package_name" =~ ^veyra-[A-Za-z0-9][A-Za-z0-9+_.-]*$ ]]; then
        echo "Refusing non-Veyra RPM in repository input: $rpm" >&2
        exit 1
    fi
    if [[ ! " ${packages[*]} " =~ " ${package_name} " ]]; then
        echo "RPM $package_name is not listed in packages/$channel/packages.list" >&2
        exit 1
    fi
    signature="$(rpm --checksig --verbose "$rpm")"
    if ! grep -Eiq 'signature.*OK' <<<"$signature"; then
        echo "Refusing unsigned or invalid RPM: $rpm" >&2
        exit 1
    fi
    cp -- "$rpm" "$repo_directory/"
done

for name in "${packages[@]}"; do
    if ! compgen -G "$repo_directory/$name-*.rpm" >/dev/null; then
        echo "No signed RPM was built for $name in the $channel channel." >&2
        exit 1
    fi
done

createrepo_c --database "$repo_directory"
