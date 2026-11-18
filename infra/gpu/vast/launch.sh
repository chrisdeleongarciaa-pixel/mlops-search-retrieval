#!/usr/bin/env bash
# Put the committed workbench on a running vast.ai VM and start a phase under its guard.
#
#   VAST_API_KEY_FILE=... HF_TOKEN_FILE=... bash infra/gpu/vast/launch.sh INSTANCE HOST PORT \
#       PRICE_PER_HOUR BUDGET_USD MAX_HOURS [PHASES]
#
# Copies HEAD as a git bundle, clones it on the VM, runs bootstrap.sh, writes the secrets to
# root-only files outside the clone (/root/.workbench.env with HF_TOKEN; /root/.vast_api_key only
# when the VM has no CONTAINER_API_KEY), starts run-all.sh and watchdog.sh detached and checks
# that both are running. The laptop-side monitor is started separately (vast.py monitor).
set -euo pipefail

INSTANCE="$1" HOST="$2" PORT="$3" PRICE="$4" BUDGET="$5" MAX_HOURS="$6" PHASES="${7:-A}"
: "${VAST_API_KEY_FILE:?}" "${HF_TOKEN_FILE:?}"
cd "$(git rev-parse --show-toplevel)"
[ -z "$(git status --porcelain --untracked-files=all)" ] || { echo "Commit first: the VM gets HEAD" >&2; exit 1; }
SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o "UserKnownHostsFile=$HOME/.ssh/known_hosts.vast"
     -o ServerAliveInterval=30 -p "$PORT" "root@$HOST")
REMOTE=/root/mlops-search-retrieval

bundle="$(mktemp -d)/workbench.bundle"
git bundle create -q "$bundle" HEAD main
scp -q -o BatchMode=yes -o "UserKnownHostsFile=$HOME/.ssh/known_hosts.vast" -P "$PORT" "$bundle" "root@$HOST:/root/workbench.bundle"
"${SSH[@]}" "set -e; [ -d $REMOTE/.git ] || git clone -q -b main /root/workbench.bundle $REMOTE;
    cd $REMOTE && git fetch -q /root/workbench.bundle main && git checkout -q main && git reset -q --hard FETCH_HEAD
    git remote remove origin 2>/dev/null || true; git log --oneline -1"

bootstrap() { "${SSH[@]}" "bash $REMOTE/infra/gpu/vast/bootstrap.sh >> /root/bootstrap.log 2>&1; status=\$?; tail -n 8 /root/bootstrap.log; exit \$status"; }
status=0; bootstrap || status=$?
if [ "$status" = 3 ]; then   # a new guest driver was installed: reboot and finish
    "${SSH[@]}" "systemctl reboot" || true
    sleep 60
    for _ in $(seq 1 40); do "${SSH[@]}" true 2>/dev/null && break; sleep 15; done
    bootstrap
elif [ "$status" != 0 ]; then
    exit "$status"
fi

# Secrets travel on stdin, never on a command line.
printf 'HF_TOKEN=%s\n' "$(cat "$HF_TOKEN_FILE")" | "${SSH[@]}" "umask 077; cat > /root/.workbench.env"
if ! "${SSH[@]}" "grep -q '^CONTAINER_API_KEY=' /etc/environment 2>/dev/null"; then
    echo "launch: the VM has no CONTAINER_API_KEY; the guard gets the account key in /root/.vast_api_key"
    "${SSH[@]}" "umask 077; cat > /root/.vast_api_key" < "$VAST_API_KEY_FILE"
fi

"${SSH[@]}" "set -e; cd $REMOTE; mkdir -p var; rm -f var/pipeline.pid var/pipeline.exit var/final.json
    set -a; . /etc/environment 2>/dev/null || true; . /root/.workbench.env; set +a
    setsid nohup env PHASES='$PHASES' B_ENGINE='${B_ENGINE:-1}' bash infra/gpu/run-all.sh </dev/null >>var/pipeline.log 2>&1 &
    unset HF_TOKEN
    setsid nohup env INSTANCE_ID=$INSTANCE BUDGET_USD=$BUDGET PRICE_PER_HOUR=$PRICE MAX_HOURS=$MAX_HOURS \
        bash infra/gpu/watchdog.sh </dev/null >>var/watchdog.log 2>&1 &
    sleep 15
    echo '--- watchdog.log'; tail -n 3 var/watchdog.log
    echo '--- pipeline.log'; tail -n 5 var/pipeline.log
    pid=\$(cat var/pipeline.pid); ps -o pid,etime,cmd -p \$pid | tail -n 1
    pgrep -f infra/gpu/watchdog.sh >/dev/null && echo 'launch: pipeline and watchdog running'"
