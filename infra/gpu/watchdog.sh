#!/usr/bin/env bash
# Stop the rented instance when the pipeline ends or the budget is spent, after packing results.
#
#   VAST_API_KEY=... INSTANCE_ID=... BUDGET_USD=5 PRICE_PER_HOUR=0.45 \
#     setsid nohup bash infra/gpu/watchdog.sh </dev/null >var/watchdog.log 2>&1 &
#
# The deadline is 90 % of BUDGET_USD / PRICE_PER_HOUR from the watchdog's start. The pipeline is
# the process whose id run-all.sh writes to $WORK/pipeline.pid (remove a stale file before
# launching); WATCH_PID overrides it. When the pipeline ends, the watchdog packs the results and
# waits GRACE_MIN minutes (default 15, never past the deadline) so they can be copied, then stops
# the instance. Stopping keeps the disk (vast.ai still bills storage); restarting needs the same
# GPU to be free. The API key is only used for the stop request to console.vast.ai.
set -uo pipefail

: "${VAST_API_KEY:?Set VAST_API_KEY}" "${INSTANCE_ID:?Set INSTANCE_ID}"
: "${BUDGET_USD:?Set BUDGET_USD}" "${PRICE_PER_HOUR:?Set PRICE_PER_HOUR}"
WORK="${WORK:-$PWD/var}"
DEADLINE_S=$(awk -v b="$BUDGET_USD" -v p="$PRICE_PER_HOUR" 'BEGIN { if (b + 0 > 0 && p + 0 > 0) printf "%d", 0.9 * b / p * 3600 }')
GRACE_MIN="${GRACE_MIN:-15}"
if ! [[ "$DEADLINE_S" =~ ^[0-9]+$ && "$DEADLINE_S" -gt 0 && "$GRACE_MIN" =~ ^[0-9]+$ ]]; then
    echo "watchdog: BUDGET_USD and PRICE_PER_HOUR must be positive numbers, GRACE_MIN a whole number" >&2
    exit 1
fi
GRACE_S=$(( GRACE_MIN * 60 ))
STARTED=$(date +%s)

if [ -z "${WATCH_PID:-}" ]; then
    for _ in $(seq 1 120); do [ -s "$WORK/pipeline.pid" ] && break; sleep 1; done
    WATCH_PID="$(cat "$WORK/pipeline.pid" 2>/dev/null || true)"
fi
echo "watchdog: deadline in $((DEADLINE_S / 60)) min; watching pid ${WATCH_PID:-none}"
if [ -z "$WATCH_PID" ]; then
    echo "watchdog: no pipeline pid after 120 s; stopping at the deadline only" >&2
fi

pack() {
    local archive="$WORK/results-$(date +%Y%m%d-%H%M%S).tar.gz"
    tar -czf "$archive" -C "$WORK" --ignore-failed-read reports pipeline.log watchdog.log \
        $(cd "$WORK" && find ann projections -name '*.json' 2>/dev/null) 2>/dev/null
    echo "watchdog: packed $archive"
}

stop_instance() {
    for attempt in 1 2 3 4 5; do
        if curl -fsS -X PUT "https://console.vast.ai/api/v0/instances/$INSTANCE_ID/" \
            -H "Authorization: Bearer $VAST_API_KEY" -H "Content-Type: application/json" \
            -d '{"state": "stopped"}'; then
            echo; echo "watchdog: stop requested"
            return 0
        fi
        sleep 20
    done
    echo "watchdog: the stop request failed five times; shutting the VM down" >&2
    sudo shutdown -h now || shutdown -h now
}

while true; do
    elapsed=$(( $(date +%s) - STARTED ))
    if [ "$elapsed" -ge "$DEADLINE_S" ]; then
        echo "watchdog: budget deadline reached after $((elapsed / 60)) min"
        break
    fi
    if [ -n "$WATCH_PID" ] && ! kill -0 "$WATCH_PID" 2>/dev/null; then
        echo "watchdog: pipeline $WATCH_PID ended after $((elapsed / 60)) min"
        pack
        remaining=$(( DEADLINE_S > elapsed ? DEADLINE_S - elapsed : 0 ))
        wait_s=$(( GRACE_S < remaining ? GRACE_S : remaining ))
        echo "watchdog: stopping the instance in $((wait_s / 60)) min"
        sleep "$wait_s"
        stop_instance
        exit 0
    fi
    sleep 30
done
pack
stop_instance
