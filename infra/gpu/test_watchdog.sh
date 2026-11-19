#!/usr/bin/env bash
# Exercise infra/gpu/watchdog.sh with a stubbed curl: bash infra/gpu/test_watchdog.sh [REPO]
GUARD="${1:-$(git rev-parse --show-toplevel)}/infra/gpu/watchdog.sh"; pass=0; fail=0

run_case() {   # NAME EXPECTED_CALLS SETUP ENV...
    local name="$1" expected="$2" setup="$3"; shift 3
    T=$(mktemp -d); mkdir -p "$T/bin" "$T/var/reports"; echo r > "$T/var/reports/x.json"
    printf '#!/bin/sh\nfor a; do case "$a" in DELETE|PUT) echo "$a" >> "%s/calls";; esac; done\n' "$T" > "$T/bin/curl"
    printf '#!/bin/sh\necho shutdown >> "%s/calls"\n' "$T" > "$T/bin/sudo"; chmod +x "$T/bin/"*
    eval "$setup"
    (cd "$T" && env PATH="$T/bin:$PATH" WORK="$T/var" REPO="$T" INSTANCE_ID=42 CONTAINER_API_KEY=k POLL_S=1 \
        GRACE_MIN=0 HOLD_MIN=0 BUDGET_USD=5 PRICE_PER_HOUR=0.45 IDLE_MIN=60 "$@" \
        timeout 150 bash "$GUARD" > "$T/out" 2>&1)
    local calls; calls="$(tr '\n' ' ' < "$T/calls" 2>/dev/null | sed 's/ $//')"
    if [ "$calls" = "$expected" ]; then pass=$((pass + 1)); verdict=ok; else fail=$((fail + 1)); verdict=FAIL; fi
    printf -- '--- %-4s %-44s calls [%s] expected [%s]\n' "$verdict" "$name" "$calls" "$expected"
    grep -E "watchdog: (success|failure|inactive|overtime|pipeline resumed)" "$T/out" | sed 's/^/       /'
}

run_case "success destroys" "DELETE" \
    '(sleep 2; echo 0 > "$T/var/pipeline.exit") & echo $! > "$T/var/pipeline.pid"'
run_case "failure without resume stops" "PUT" \
    '(sleep 2; echo 3 > "$T/var/pipeline.exit") & echo $! > "$T/var/pipeline.pid"'
run_case "vanished pipeline without resume stops" "PUT" \
    '(sleep 2) & echo $! > "$T/var/pipeline.pid"'
# A failure, then run-all.sh started again (new pid, pipeline.exit removed), then success.
run_case "failure, resume, success destroys" "DELETE" \
    'printf "#!/bin/sh\necho \$\$ > %s/var/pipeline.pid\nrm -f %s/var/pipeline.exit\nsleep 4\necho 0 > %s/var/pipeline.exit\n" "$T" "$T" "$T" > "$T/resume.sh"
     (sleep 2; echo 3 > "$T/var/pipeline.exit"; sleep 4; sh "$T/resume.sh") & echo $! > "$T/var/pipeline.pid"' \
    HOLD_MIN=1
run_case "overtime stops" "PUT" \
    '(sleep 120) & echo $! > "$T/var/pipeline.pid"' MAX_HOURS=0.001
run_case "inactive without resume stops" "PUT" \
    '(sleep 120) & echo $! > "$T/var/pipeline.pid"' IDLE_MIN=0 IDLE_LOAD=1000
run_case "invalid hold minutes refuses to start" "" \
    'echo 1 > "$T/var/pipeline.pid"' HOLD_MIN=x
# KEEP_DISK=1 stops after success, and the archive carries the oracle arrays and vector-set records.
run_case "success with KEEP_DISK stops" "PUT" \
    'mkdir -p "$T/var/ann/x/oracle/cohort-1000" "$T/var/vectors/s/document-d"
     echo a > "$T/var/ann/x/oracle/cohort-1000/rows.npy"; echo m > "$T/var/vectors/s/document-d/manifest.json"
     echo z > "$T/var/vectors/s/document-d/shard-00000.npy"
     (sleep 2; echo 0 > "$T/var/pipeline.exit") & echo $! > "$T/var/pipeline.pid"' KEEP_DISK=1
packed="$(tar -tzf "$(ls "$T"/var/results-*.tar.gz | head -n 1)" | sort | tr '\n' ' ')"
case "$packed" in
    *"ann/x/oracle/cohort-1000/rows.npy"*"vectors/s/document-d/manifest.json"*) case "$packed" in
        *shard-00000.npy*) fail=$((fail + 1)); echo "--- FAIL archive holds vector shards" ;;
        *) pass=$((pass + 1)); echo "--- ok   archive holds the oracle arrays and vector-set records, not the shards" ;;
        esac ;;
    *) fail=$((fail + 1)); echo "--- FAIL archive contents: $packed" ;;
esac
echo "passed $pass, failed $fail"
[ "$fail" = 0 ]
