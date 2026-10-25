#!/usr/bin/env bash
set -euo pipefail

# mkosi's DNF backend uses host config. Do not let it inherit host repositories
# or caches: build and image-shell commands must run in the checked Fedora
# container, with only the official repositories for mkosi.conf's target.
case " $* " in
    *" build "*|*" shell "*)
        bash "$(dirname -- "$0")/check-build-environment.sh"
        python3 "$(dirname -- "$0")/check-build-environment.py"
        ;;
esac

exec uv tool run \
    --from git+https://github.com/systemd/mkosi@47560954a66c0a992e1a6ef9711177a8fc3ec5e6 \
    mkosi "$@"
