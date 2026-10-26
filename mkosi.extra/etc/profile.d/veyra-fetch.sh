# Give Screenfetch the Veyra mark when the optional program is installed.
# Explicit user choices for another art source or no art are preserved.
if command -v screenfetch >/dev/null 2>&1; then
    screenfetch() {
        local veyra_fetch_arg
        for veyra_fetch_arg in "$@"; do
            case "$veyra_fetch_arg" in
                -a|-A|-n)
                    command screenfetch "$@"
                    return
                    ;;
            esac
        done
        command screenfetch -a /usr/share/veyra/screenfetch/logo.sh "$@"
    }
fi
