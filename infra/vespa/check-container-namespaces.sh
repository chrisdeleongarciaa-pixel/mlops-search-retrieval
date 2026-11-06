#!/usr/bin/env bash
# Probe Linux isolation primitives without changing the parent namespaces.
set -u

for program in timeout unshare ip python3; do
    if ! command -v "$program" >/dev/null; then
        printf 'Missing required tool: %s\n' "$program" >&2
        exit 2
    fi
done

failed=0
probe() {
    local label=$1 status=0
    shift
    timeout --kill-after=1s 5s "$@" || status=$?
    printf '%s exit=%s\n' "$label" "$status"
    if ((status != 0)); then
        failed=1
    fi
}

probe rootless_network unshare --user --map-root-user --net -- bash -ec '
    ip link set lo up
    python3 -c "import socket; s=socket.socket(); s.bind((\"127.0.0.1\", 0)); s.listen()"
'
probe rootless_pid_and_proc unshare --user --map-root-user --mount --pid --fork --mount-proc -- bash -ec '
    test "$$" -eq 1
    test "$(cat /proc/self/comm)" = cat
'
exit "$failed"
