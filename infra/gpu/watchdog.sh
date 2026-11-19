#!/usr/bin/env bash
# Guard of a rented instance: ends it after success, keeps it for a fix after a failure, and
# stops it at the time or budget limit.
#
#   INSTANCE_ID=... BUDGET_USD=2 PRICE_PER_HOUR=0.57 MAX_HOURS=4 \
#     setsid nohup bash infra/gpu/watchdog.sh </dev/null >var/watchdog.log 2>&1 &
#
# Triggers, checked every POLL_S seconds (30):
#   success   run-all.sh wrote 0 to $WORK/pipeline.exit                   -> destroy (stop with KEEP_DISK=1)
#   failure   it wrote another status, or its process vanished             -> hold, then stop
#   inactive  no file under $WORK changed for IDLE_MIN minutes (45) while
#             the 1-minute load average stayed under IDLE_LOAD (1.0)       -> hold, then stop
#   overtime  GRACE_MIN before the deadline: MAX_HOURS since start, or
#             90 % of BUDGET_USD / PRICE_PER_HOUR                         -> stop
# Every trigger first packs the reports, logs, JSON records and the commits made on the host into
# $WORK/results-*.tar.gz and writes $WORK/final.json with the action, which the laptop monitor reads.
#
# hold: a failed or idle pipeline keeps its vectors, indexes and stages on disk, so the instance
# is kept HOLD_MIN minutes (60, never past the overtime point) for a fix. Running run-all.sh again
# resumes from the failed stage; the guard sees its new process and goes back to watching it.
# Without a resume the instance is stopped, not destroyed: the disk is kept for a later restart.
# success and stop wait GRACE_MIN minutes (15) first so the archive can be copied.
#
# The API key is CONTAINER_API_KEY when vast.ai provides one, else the file VAST_API_KEY_FILE
# (default /root/.vast_api_key, mode 600). It is used only for requests to console.vast.ai. If
# the API refuses five times, the VM is shut down.
set -uo pipefail

: "${INSTANCE_ID:?Set INSTANCE_ID}" "${BUDGET_USD:?Set BUDGET_USD}" "${PRICE_PER_HOUR:?Set PRICE_PER_HOUR}"
WORK="${WORK:-$PWD/var}"
REPO="${REPO:-$PWD}"
POLL_S="${POLL_S:-30}"
IDLE_MIN="${IDLE_MIN:-45}"
IDLE_LOAD="${IDLE_LOAD:-1.0}"
GRACE_MIN="${GRACE_MIN:-15}"
HOLD_MIN="${HOLD_MIN:-60}"
# KEEP_DISK=1: stop instead of destroying after success, when the disk holds data a later run needs
# (the vector shards are not in the archive).
KEEP_DISK="${KEEP_DISK:-0}"
MAX_HOURS="${MAX_HOURS:-0}"
API="${VAST_API_URL:-https://console.vast.ai/api/v0}"

DEADLINE_S=$(awk -v b="$BUDGET_USD" -v p="$PRICE_PER_HOUR" -v m="$MAX_HOURS" 'BEGIN {
    if (b + 0 <= 0 || p + 0 <= 0 || m + 0 < 0) exit
    d = 0.9 * b / p * 3600; if (m + 0 > 0 && m * 3600 < d) d = m * 3600; printf "%d", d }')
if ! [[ "$DEADLINE_S" =~ ^[0-9]+$ && "$DEADLINE_S" -gt 0 && "$GRACE_MIN" =~ ^[0-9]+$ && "$HOLD_MIN" =~ ^[0-9]+$ \
        && "$IDLE_MIN" =~ ^[0-9]+$ && "$POLL_S" =~ ^[0-9]+$ ]]; then
    echo "watchdog: BUDGET_USD and PRICE_PER_HOUR must be positive, MAX_HOURS non-negative," \
         "GRACE_MIN, HOLD_MIN, IDLE_MIN and POLL_S whole numbers" >&2
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
IDLE_SINCE=$STARTED   # inactivity is measured from here; a resume resets it

if [ -z "${WATCH_PID:-}" ]; then
    for _ in $(seq 1 120); do [ -s "$WORK/pipeline.pid" ] && break; sleep 1; done
    WATCH_PID="$(cat "$WORK/pipeline.pid" 2>/dev/null || true)"
fi
echo "watchdog: destroy after success; hold $HOLD_MIN min then stop after a failure or $IDLE_MIN min idle;" \
     "stop at $((OVERTIME_S / 60)) min; watching pid ${WATCH_PID:-none}; started $(date -u +%FT%TZ)"

elapsed() { echo $(( $(date +%s) - STARTED )); }

pack() {
    local archive
    archive="$WORK/results-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
    if [ -s "$WORK/code-commit" ]; then
        rm -rf "$WORK/patches"
        git -C "$REPO" format-patch -q -o "$WORK/patches" "$(cat "$WORK/code-commit")..HEAD" 2>/dev/null || true
    fi
    # JSON records of oracles, sweeps, projections and vector sets, and the oracle arrays themselves
    # (rows.npy and scores.npy, about 11 MB per MS MARCO cohort); the vector shards stay on disk.
    # The engine runs' outputs (run.trec, per-query records, manifests) live in the clone.
    (cd "$WORK" && tar -czf "$archive" --ignore-failed-read reports stages patches pipeline.log watchdog.log \
        pipeline.exit $(find ann projections labels vectors -name '*.json' -o -type f -path 'labels/*' \
                        -o -path '*/oracle/cohort-*' -name '*.npy' 2>/dev/null) \
        -C "$REPO" $(cd "$REPO" && find evaluation/runs -mindepth 2 -maxdepth 2 \( -name run.trec \
                     -o -name per-query.jsonl -o -name manifest.json -o -name timings.json \) 2>/dev/null) 2>/dev/null)
    echo "watchdog: packed $archive ($(du -h "$archive" | cut -f1))"
}

record() {   # record REASON ACTION: the decision the laptop monitor reads
    printf '{"reason": "%s", "elapsed_s": %d, "pipeline_exit": "%s", "action": "%s", "at": "%s"}\n' \
        "$1" "$(elapsed)" "$(cat "$WORK/pipeline.exit" 2>/dev/null || echo none)" "$2" "$(date -u +%FT%TZ)" \
        > "$WORK/final.json.part" && mv "$WORK/final.json.part" "$WORK/final.json"
}

api_call() {   # api_call METHOD BODY
    curl -fsS -m 60 -X "$1" "$API/instances/$INSTANCE_ID/" -H "Authorization: Bearer $KEY" \
        -H "Content-Type: application/json" -d "$2"
}

end_instance() {   # end_instance destroy|stop
    for attempt in 1 2 3 4 5; do
        if [ "$1" = destroy ] && api_call DELETE '{}'; then echo; echo "watchdog: destroy requested"; return 0; fi
        if [ "$1" = stop ] && api_call PUT '{"state": "stopped"}'; then echo; echo "watchdog: stop requested"; return 0; fi
        echo "watchdog: $1 request $attempt failed" >&2
        sleep 20
    done
    echo "watchdog: the API refused five times; shutting the VM down" >&2
    sudo shutdown -h now || shutdown -h now
}

idle() {   # true when nothing under $WORK changed for IDLE_MIN minutes and the load is low
    local since recent load
    since=$(( $(date +%s) - IDLE_MIN * 60 ))
    [ "$since" -ge "$IDLE_SINCE" ] || return 1
    recent=$(find "$WORK" -type f -newermt "@$since" ! -name watchdog.log ! -name 'results-*' ! -name final.json \
        2>/dev/null | head -n 1)
    [ -z "$recent" ] || return 1
    load=$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)
    awk -v l="$load" -v t="$IDLE_LOAD" 'BEGIN { exit !(l + 0 < t + 0) }'
}

finish() {   # finish REASON destroy|stop: pack, record, wait for the copy, end the instance
    local remaining wait_s
    echo "watchdog: $1 after $(( $(elapsed) / 60 )) min"
    pack
    record "$1" "$2"
    remaining=$(( DEADLINE_S > $(elapsed) ? DEADLINE_S - $(elapsed) : 0 ))
    wait_s=$(( GRACE_MIN * 60 < remaining ? GRACE_MIN * 60 : remaining ))
    echo "watchdog: $2 in $((wait_s / 60)) min unless the monitor acts first"
    sleep "$wait_s"
    end_instance "$2"
    exit 0
}

resumed() {   # resumed new|any: true when run-all.sh was started again
    # run-all.sh removes pipeline.exit and records its pid when it starts. After a failure that is
    # the signal (a pid may be reused); a hung pipeline is still alive, so then the pid must differ.
    local pid
    pid="$(cat "$WORK/pipeline.pid" 2>/dev/null || true)"
    [ -n "$pid" ] && [ ! -f "$WORK/pipeline.exit" ] && kill -0 "$pid" 2>/dev/null || return 1
    [ "$1" = any ] || [ "$pid" != "$WATCH_PID" ]
}

hold() {   # hold REASON new|any: keep the instance for a fix, then stop it
    local until
    echo "watchdog: $1 after $(( $(elapsed) / 60 )) min; holding the instance for a fix"
    pack
    record "$1" hold
    until=$(( $(elapsed) + HOLD_MIN * 60 )); [ "$until" -le "$OVERTIME_S" ] || until=$OVERTIME_S
    while [ "$(elapsed)" -lt "$until" ]; do
        if resumed "$2"; then
            WATCH_PID="$(cat "$WORK/pipeline.pid")"; IDLE_SINCE=$(date +%s)
            rm -f "$WORK/final.json"
            echo "watchdog: pipeline resumed as pid $WATCH_PID after $(( $(elapsed) / 60 )) min"
            return 0
        fi
        sleep "$POLL_S"
    done
    finish "$1; not resumed" stop
}

while true; do
    if [ -f "$WORK/pipeline.exit" ]; then
        status="$(cat "$WORK/pipeline.exit")"
        if [ "$status" = 0 ]; then [ "$KEEP_DISK" = 1 ] && finish success stop; finish success destroy; fi
        hold "failure (exit $status)" any; continue
    fi
    if [ -n "${WATCH_PID:-}" ] && ! kill -0 "$WATCH_PID" 2>/dev/null; then
        sleep 2; [ -f "$WORK/pipeline.exit" ] && continue
        hold "failure (pipeline $WATCH_PID vanished)" any; continue
    fi
    [ "$(elapsed)" -ge "$OVERTIME_S" ] && finish overtime stop
    if idle; then hold inactive new; continue; fi
    sleep "$POLL_S"
done
