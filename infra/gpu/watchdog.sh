#!/usr/bin/env bash
# Guard of a rented instance: ends it when the pipeline succeeds, fails, goes idle or overruns.
#
#   INSTANCE_ID=... BUDGET_USD=4.4 PRICE_PER_HOUR=0.41 MAX_HOURS=7 \
#     setsid nohup bash infra/gpu/watchdog.sh </dev/null >var/watchdog.log 2>&1 &
#
# Triggers, checked every POLL_S seconds (30):
#   success   run-all.sh wrote 0 to $WORK/pipeline.exit
#   failure   it wrote another status, or its process (from $WORK/pipeline.pid) vanished
#   overtime  GRACE_MIN before the deadline: MAX_HOURS since start, or 90 % of BUDGET_USD / PRICE_PER_HOUR
#   inactive  no file under $WORK changed for IDLE_MIN minutes (45) while the 1-minute load
#             average stayed under IDLE_LOAD (1.0)
# On any trigger it packs the reports, logs, JSON records and the commits made on the host into
# $WORK/results-*.tar.gz, writes $WORK/final.json (which the laptop monitor waits for), waits
# GRACE_MIN minutes (15, never past the deadline) for the archive to be copied, and then applies
# ACTION: destroy (default; deletes the instance and its disk) or stop (keeps the disk, billed).
#
# The API key is CONTAINER_API_KEY when vast.ai provides one, else the file VAST_API_KEY_FILE
# (default /root/.vast_api_key, mode 600). It is used only for requests to console.vast.ai. If
# the API refuses five times, the VM is shut down.
set -uo pipefail

: "${INSTANCE_ID:?Set INSTANCE_ID}" "${BUDGET_USD:?Set BUDGET_USD}" "${PRICE_PER_HOUR:?Set PRICE_PER_HOUR}"
WORK="${WORK:-$PWD/var}"
REPO="${REPO:-$PWD}"
ACTION="${ACTION:-destroy}"
POLL_S="${POLL_S:-30}"
IDLE_MIN="${IDLE_MIN:-45}"
IDLE_LOAD="${IDLE_LOAD:-1.0}"
GRACE_MIN="${GRACE_MIN:-15}"
MAX_HOURS="${MAX_HOURS:-0}"
API="${VAST_API_URL:-https://console.vast.ai/api/v0}"

DEADLINE_S=$(awk -v b="$BUDGET_USD" -v p="$PRICE_PER_HOUR" -v m="$MAX_HOURS" 'BEGIN {
    if (b + 0 <= 0 || p + 0 <= 0 || m + 0 < 0) exit
    d = 0.9 * b / p * 3600; if (m + 0 > 0 && m * 3600 < d) d = m * 3600; printf "%d", d }')
if ! [[ "$DEADLINE_S" =~ ^[0-9]+$ && "$DEADLINE_S" -gt 0 && "$GRACE_MIN" =~ ^[0-9]+$ \
        && "$IDLE_MIN" =~ ^[0-9]+$ && "$POLL_S" =~ ^[0-9]+$ ]] || [[ "$ACTION" != destroy && "$ACTION" != stop ]]; then
    echo "watchdog: BUDGET_USD and PRICE_PER_HOUR must be positive, MAX_HOURS non-negative," \
         "GRACE_MIN, IDLE_MIN and POLL_S whole numbers, ACTION destroy or stop" >&2
    exit 1
fi
if [ -n "${CONTAINER_API_KEY:-}" ]; then
    KEY="$CONTAINER_API_KEY"
else
    KEY="$(cat "${VAST_API_KEY_FILE:-/root/.vast_api_key}" 2>/dev/null || true)"
fi
[ -n "$KEY" ] || { echo "watchdog: no vast.ai API key (CONTAINER_API_KEY or VAST_API_KEY_FILE)" >&2; exit 1; }
STARTED=$(date +%s)
# Overtime fires a grace period before the deadline, so the archive can still be copied within
# the budget; at the deadline itself the grace would be cut to zero and the copy lost.
OVERTIME_S=$(( DEADLINE_S > GRACE_MIN * 60 ? DEADLINE_S - GRACE_MIN * 60 : 0 ))

if [ -z "${WATCH_PID:-}" ]; then
    for _ in $(seq 1 120); do [ -s "$WORK/pipeline.pid" ] && break; sleep 1; done
    WATCH_PID="$(cat "$WORK/pipeline.pid" 2>/dev/null || true)"
fi
echo "watchdog: $ACTION after success, failure, $IDLE_MIN min idle or $((DEADLINE_S / 60)) min;" \
     "watching pid ${WATCH_PID:-none}; started $(date -u +%FT%TZ)"

pack() {
    local archive
    archive="$WORK/results-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
    if [ -s "$WORK/code-commit" ]; then
        rm -rf "$WORK/patches"
        git -C "$REPO" format-patch -q -o "$WORK/patches" "$(cat "$WORK/code-commit")..HEAD" 2>/dev/null || true
    fi
    (cd "$WORK" && tar -czf "$archive" --ignore-failed-read reports stages patches pipeline.log watchdog.log \
        pipeline.exit $(find ann projections labels -name '*.json' -o -type f -path 'labels/*' 2>/dev/null) 2>/dev/null)
    echo "watchdog: packed $archive ($(du -h "$archive" | cut -f1))"
}

api_call() {   # api_call METHOD BODY
    curl -fsS -m 60 -X "$1" "$API/instances/$INSTANCE_ID/" -H "Authorization: Bearer $KEY" \
        -H "Content-Type: application/json" -d "$2"
}

end_instance() {
    for attempt in 1 2 3 4 5; do
        if [ "$ACTION" = destroy ] && api_call DELETE '{}'; then echo; echo "watchdog: destroy requested"; return 0; fi
        if [ "$ACTION" = stop ] && api_call PUT '{"state": "stopped"}'; then echo; echo "watchdog: stop requested"; return 0; fi
        echo "watchdog: $ACTION request $attempt failed" >&2
        sleep 20
    done
    echo "watchdog: the API refused five times; shutting the VM down" >&2
    sudo shutdown -h now || shutdown -h now
}

idle() {   # true when nothing under $WORK changed for IDLE_MIN minutes and the load is low
    local since recent load
    since=$(( $(date +%s) - IDLE_MIN * 60 ))
    [ $(( $(date +%s) - STARTED )) -ge $(( IDLE_MIN * 60 )) ] || return 1
    recent=$(find "$WORK" -type f -newermt "@$since" ! -name watchdog.log 2>/dev/null | head -n 1)
    [ -z "$recent" ] || return 1
    load=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)
    awk -v l="$load" -v t="$IDLE_LOAD" 'BEGIN { exit !(l + 0 < t + 0) }'
}

finish() {   # finish REASON
    local elapsed remaining wait_s
    elapsed=$(( $(date +%s) - STARTED ))
    echo "watchdog: $1 after $((elapsed / 60)) min"
    pack
    printf '{"reason": "%s", "elapsed_s": %d, "pipeline_exit": "%s", "action": "%s", "at": "%s"}\n' \
        "$1" "$elapsed" "$(cat "$WORK/pipeline.exit" 2>/dev/null || echo none)" "$ACTION" "$(date -u +%FT%TZ)" \
        > "$WORK/final.json.part" && mv "$WORK/final.json.part" "$WORK/final.json"
    remaining=$(( DEADLINE_S > elapsed ? DEADLINE_S - elapsed : 0 ))
    wait_s=$(( GRACE_MIN * 60 < remaining ? GRACE_MIN * 60 : remaining ))
    echo "watchdog: $ACTION in $((wait_s / 60)) min unless the monitor ends the instance first"
    sleep "$wait_s"
    end_instance
    exit 0
}

while true; do
    elapsed=$(( $(date +%s) - STARTED ))
    if [ -f "$WORK/pipeline.exit" ]; then
        status="$(cat "$WORK/pipeline.exit")"
        if [ "$status" = 0 ]; then finish success; else finish "failure (exit $status)"; fi
    fi
    if [ -n "${WATCH_PID:-}" ] && ! kill -0 "$WATCH_PID" 2>/dev/null; then
        sleep 2; [ -f "$WORK/pipeline.exit" ] && continue
        finish "failure (pipeline $WATCH_PID vanished)"
    fi
    [ "$elapsed" -ge "$OVERTIME_S" ] && finish overtime
    idle && finish inactive
    sleep "$POLL_S"
done
