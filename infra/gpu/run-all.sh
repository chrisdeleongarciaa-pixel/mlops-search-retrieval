#!/usr/bin/env bash
# The reduced workbench, end to end, on one vast.ai VM with an NVIDIA GPU.
#
#   HF_TOKEN=... PHASES="A B" bash infra/gpu/run-all.sh
#
# Launch it detached, with the watchdog (see infra/gpu/README.md):
#   setsid nohup env HF_TOKEN=... bash infra/gpu/run-all.sh </dev/null >var/pipeline.log 2>&1 &
#
# Phase A deploys the LitSearch system and measures it: vector sets and oracles of SciFact and
# LitSearch, the Vespa projections and their parity with the oracle, the BM25, dense and fused
# runs (fusion chosen on dev, reported once on test), TEI parity, ACL-01 on SciFact and SRV-01
# under open-loop load. Phase B adds MS MARCO: embedding, oracle, HNSW sweeps, projection,
# parity, dev runs and the calibration of Vespa BM25 against Anserini.
#
# Needs a VM (Docker inside the instance) with the NVIDIA container runtime, 64 GB of RAM and
# 150 GB of disk for phase B, from a clean checkout. Every stage is recorded once finished in
# var/stages; run the script again to resume.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
WORK="${WORK:-$PWD/var}"
PHASES="${PHASES:-A B}"
export WORK
mkdir -p "$WORK/stages" "$WORK/reports" "$WORK/projections"
# The watchdog follows this process. setsid forks when launched from an interactive shell, so the
# launcher's $! is not this process; record it here, before anything can fail. The exit status
# goes to pipeline.exit so the watchdog can tell success from failure.
rm -f "$WORK/pipeline.exit"
echo $$ > "$WORK/pipeline.pid"
trap 'echo $? > "$WORK/pipeline.exit"' EXIT
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo "The work tree must be clean." >&2
    exit 1
fi
CODE_COMMIT_FILE="$WORK/code-commit"
[ -f "$CODE_COMMIT_FILE" ] || git rev-parse HEAD > "$CODE_COMMIT_FILE"
CODE_COMMIT="$(cat "$CODE_COMMIT_FILE")"

stage() {   # stage NAME COMMAND...: run once, record completion.
    local name="$1"; shift
    if [ -f "$WORK/stages/$name.done" ]; then echo "== $name (done)"; return 0; fi
    echo "== $name ($(date -u +%H:%M:%S))"
    "$@"
    touch "$WORK/stages/$name.done"
}

commit_declarations() {
    git add config/runs/generated
    git -c user.name="workbench host" -c user.email="workbench@localhost" commit -q -m "$1" \
        -m "Generated on the GPU host by infra/gpu/run-all.sh; declarations only."
}

# --- Toolchain: CPython 3.12.10, as the runtime manifest pins it.
command -v nvidia-smi >/dev/null || { echo "No NVIDIA driver" >&2; exit 1; }
docker info --format '{{json .Runtimes}}' | grep -q nvidia || {
    echo "Docker has no NVIDIA runtime: install the NVIDIA Container Toolkit on the VM" >&2; exit 1; }
if ! command -v uv >/dev/null; then
    python3 -m pip install --user uv==0.12.2 || { sudo apt-get update && sudo apt-get install -y python3-pip && python3 -m pip install --user uv==0.12.2; }
    export PATH="$HOME/.local/bin:$PATH"
fi
uv python install 3.12.10
export PYTHON="$(uv python find 3.12.10)"
P="$WORK/venv/bin/python"

# --- Serving stack.
CAP="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1)"
case "$CAP" in
    8.0) TEI_DIGEST=sha256:02864b69c15e4f247b8306b0be8801d6800fed97702185fb19d98170e956fab1 ;;   # 1.9.4, A100
    8.6) TEI_DIGEST=sha256:bee0bdb887096229add91b6fdfcf95258c12eb9180e90dc86f3b4b25b86fc613 ;;   # 86-1.9.4
    8.9) TEI_DIGEST=sha256:1a284d9ca1adcc20b78c261d4d052c06057f0a3cb49a15c5d2c00930f710fce2 ;;   # 89-1.9.4
    9.0) TEI_DIGEST=sha256:b1f4a9555fd393ff2ec59e89cdcb69476f723c80b773760fae15c99ed211db30 ;;   # hopper-1.9.4
    *) echo "No pinned TEI image for compute capability $CAP" >&2; exit 1 ;;
esac
export TEI_IMAGE="ghcr.io/huggingface/text-embeddings-inference@$TEI_DIGEST"
export MODEL_DIR="$WORK/models/embeddinggemma-300m"
[ -f "$WORK/pg-password" ] || python3 -c "import secrets; print(secrets.token_hex(16))" > "$WORK/pg-password"
export RETRIEVAL_LAB_PASSWORD="$(cat "$WORK/pg-password")"
DSN="postgresql://postgres:$RETRIEVAL_LAB_PASSWORD@127.0.0.1:5432/retrieval_lab"
COMPOSE="docker compose -f infra/deploy/compose.yaml"
VESPA=http://127.0.0.1:8080
export BENCH_VESPA_URL="$VESPA" BENCH_PROJECTIONS_DIR="$WORK/projections" BENCH_VECTORS_DIR="$WORK/vectors"
SPACE=manifests/space/embeddinggemma-300m-fp32.json
FIELDS=manifests/transform/beir-title-text-fields-v1.json
RUNTIME=manifests/runtime/gpu-linux-x86_64.json

wait_for() {   # wait_for URL SECONDS
    for _ in $(seq 1 "$2"); do curl -fsS "$1" >/dev/null 2>&1 && return 0; sleep 1; done
    echo "Timed out waiting for $1" >&2; return 1
}

stack_up() {
    $COMPOSE up -d postgres vespa
    for _ in $(seq 1 120); do $COMPOSE exec -T postgres pg_isready -U postgres -d retrieval_lab >/dev/null 2>&1 && break; sleep 1; done
    wait_for http://127.0.0.1:19071/state/v1/health 300
}
migrate() {
    for migration in infra/postgres/migrations/000*.sql; do
        $COMPOSE exec -T postgres psql -X -v ON_ERROR_STOP=1 -U postgres -d retrieval_lab < "$migration"
    done
}
deploy_vespa() {
    "$P" infra/vespa/deploy.py http://127.0.0.1:19071
    wait_for "$VESPA/ApplicationStatus" 600
}

project() {   # project CONFIG TEXT_TRANSFORM NAME: feeds and records the collection label in var/labels/NAME
    mkdir -p "$WORK/labels"
    "$P" evaluation/vespa_project.py "$1" --vectors-dir "$WORK/vectors" --data-dir "$WORK/data" \
        --out-dir "$WORK/projections" --vespa-url "$VESPA" --text-transform "$2" --threads "${FEED_THREADS:-32}" \
        | tail -n 1 | "$P" -c "import json, sys; print(json.load(sys.stdin)['collection'])" > "$WORK/labels/$3"
}
label() { cat "$WORK/labels/$1"; }

parity() {    # parity CONFIG NAME SAMPLE
    "$P" evaluation/engine_parity.py "$1" --collection "$(label "$2")" --vectors-dir "$WORK/vectors" \
        --out-dir "$WORK/ann" --projections-dir "$WORK/projections" --vespa-url "$VESPA" --exact-sample "$3" \
        | tee "$WORK/reports/engine-parity-$2.jsonl"         || echo "Engine parity did not pass for $2: see $WORK/reports/engine-parity-$2.jsonl" >&2
}

bench_run() { # bench_run CONFIG: prints the output directory
    "$P" evaluation/bench.py run "$1" --data-dir "$WORK/data" | tail -n 1 \
        | "$P" -c "import json, sys; print(json.load(sys.stdin)['output'])"
}
bench_report() { # bench_report TEST_CONFIG DEV_CONFIG
    "$P" evaluation/bench.py report "$1" --frozen-dev "$2" --data-dir "$WORK/data" | tail -n 1 \
        | "$P" -c "import json, sys; print(json.load(sys.stdin)['output'])"
}

declare_runs() { "$P" evaluation/declare_runs.py --code-commit "$CODE_COMMIT" --runtime "$RUNTIME" "$@" >/dev/null; }

phase_a() {
    # ONLY_MSMARCO belongs to phase B; phase A always builds its own SciFact and LitSearch sets.
    stage step3-litsearch env ONLY_MSMARCO=0 bash infra/gpu/step3-embedding.sh
    stage step4-litsearch env ONLY_MSMARCO=0 SCIFACT_ONLY=1 bash infra/gpu/step4-ann.sh
    stage stack stack_up
    stage migrate migrate
    stage vespa-deploy deploy_vespa
    stage project-scifact project config/ann/scifact-embeddinggemma-300m-fp32.toml \
        manifests/transform/beir-title-space-text-v1.json scifact
    stage project-litsearch project config/ann/litsearch-embeddinggemma-300m-fp32.toml \
        manifests/transform/beir-title-space-text-v1.json litsearch
    stage parity-litsearch parity config/ann/litsearch-embeddinggemma-300m-fp32.toml litsearch 300

    local lit; lit="$(label litsearch)"
    local common=(--name litsearch --collection "$lit" --corpus manifests/corpus/litsearch.json --transform "$FIELDS" --space "$SPACE")
    if [ ! -f "$WORK/stages/declared-litsearch-dev.done" ]; then
        declare_runs "${common[@]}" --queries manifests/queries/litsearch-dev.json
        commit_declarations "Declare LitSearch development runs of collection $lit"
        touch "$WORK/stages/declared-litsearch-dev.done"
    fi
    local gen=config/runs/generated
    if [ ! -f "$WORK/stages/dev-runs.done" ]; then
        bench_run "$gen/litsearch-dev.bm25.toml" > "$WORK/stages/dev-bm25.out"
        bench_run "$gen/litsearch-dev.dense.toml" > "$WORK/stages/dev-dense.out"
        touch "$WORK/stages/dev-runs.done"
    fi
    if [ ! -f "$WORK/stages/fusion-select.done" ]; then
        "$P" evaluation/fusion_sweep.py select "$(cat "$WORK/stages/dev-bm25.out")" "$(cat "$WORK/stages/dev-dense.out")" \
            --data-dir "$WORK/data" --out "$WORK/reports/fusion-dev.json"
        "$P" -c "import json; c = json.load(open('$WORK/reports/fusion-dev.json'))['chosen']['method']; print('rrf' if c['kind'] == 'rrf' else 'convex:%g' % c['alpha'])" \
            > "$WORK/stages/hybrid"
        touch "$WORK/stages/fusion-select.done"
    fi
    local hybrid; hybrid="$(cat "$WORK/stages/hybrid")"
    local hybrid_name="hybrid-${hybrid/:/-}"
    if [ ! -f "$WORK/stages/declared-litsearch-hybrid-dev.done" ]; then
        declare_runs "${common[@]}" --queries manifests/queries/litsearch-dev.json --systems --hybrid "$hybrid"
        commit_declarations "Freeze the fusion chosen on LitSearch development: $hybrid"
        touch "$WORK/stages/declared-litsearch-hybrid-dev.done"
    fi
    stage dev-hybrid sh -c "'$P' evaluation/bench.py run '$gen/litsearch-dev.$hybrid_name.toml' --data-dir '$WORK/data' > '$WORK/reports/litsearch-dev-hybrid.json'"
    if [ ! -f "$WORK/stages/declared-litsearch-test.done" ]; then
        declare_runs "${common[@]}" --queries manifests/queries/litsearch-test.json --hybrid "$hybrid"
        commit_declarations "Declare LitSearch test reports against the frozen development runs"
        touch "$WORK/stages/declared-litsearch-test.done"
    fi
    if [ ! -f "$WORK/stages/test-reports.done" ]; then
        for system in bm25 dense "$hybrid_name"; do
            bench_report "$gen/litsearch-test.$system.toml" "$gen/litsearch-dev.$system.toml" > "$WORK/stages/test-$system.out"
        done
        "$P" evaluation/fusion_sweep.py gate "$(cat "$WORK/stages/test-$hybrid_name.out")/per-query.jsonl" \
            "$(cat "$WORK/stages/test-bm25.out")/per-query.jsonl" "$(cat "$WORK/stages/test-dense.out")/per-query.jsonl" \
            --out "$WORK/reports/fusion-test-gate.json"
        for system in bm25 dense "$hybrid_name"; do cp "$(cat "$WORK/stages/test-$system.out")/manifest.json" \
            "$WORK/reports/litsearch-test-$system-manifest.json"; done
        touch "$WORK/stages/test-reports.done"
    fi
    # The same test runs in the measures of the LitSearch paper (R@5 specific, R@20 broad).
    stage litsearch-compare "$P" evaluation/litsearch_compare.py "bm25=$(cat "$WORK/stages/test-bm25.out")" \
        "dense=$(cat "$WORK/stages/test-dense.out")" "$hybrid_name=$(cat "$WORK/stages/test-$hybrid_name.out")" \
        --queries manifests/queries/litsearch-test.json --corpus manifests/corpus/litsearch.json \
        --data-dir "$WORK/data" --out "$WORK/reports/litsearch-compare.json"

    stage tei-up sh -c "$COMPOSE --profile tei up -d tei"
    stage tei-health wait_for http://127.0.0.1:8081/health 900
    for queries in litsearch-test litsearch-dev; do
        stage "tei-parity-$queries" sh -c "'$P' evaluation/tei_parity.py --tei-url http://127.0.0.1:8081 --space $SPACE \
            --corpus manifests/corpus/litsearch.json --queries manifests/queries/$queries.json --transform $FIELDS \
            --vectors-dir '$WORK/vectors' --data-dir '$WORK/data' | tee '$WORK/reports/tei-parity-$queries.json'             || echo 'TEI parity did not pass for $queries' >&2"
    done
    stage acl-01 sh -c "'$P' evaluation/acl_revocation.py config/ann/scifact-embeddinggemma-300m-fp32.toml \
        --collection '$(label scifact)' --vectors-dir '$WORK/vectors' --projections-dir '$WORK/projections' \
        --vespa-url $VESPA --postgres-dsn '$DSN' --out '$WORK/reports/acl-01.json' || true"
    stage srv-01 serve_and_load "$lit" "$hybrid" parallel srv-01 ${SRV_ROUTES:-bm25 dense hybrid}
    # The hybrid route with its branches one after the other, on the same host: the baseline of
    # the parallel branches above.
    stage srv-01-sequential serve_and_load "$lit" "$hybrid" sequential srv-01-sequential hybrid
}

serve_and_load() {   # serve_and_load COLLECTION FUSION BRANCHES OUT_DIR ROUTE...
    local cpus; cpus="$(nproc)"
    local generator="${GENERATOR_CPUS:-0-1}"
    local collection="$1" fusion="$2" branches="$3" out="$4"; shift 4
    taskset -c "2-$((cpus - 1))" "$P" -m search_retrieval.serving.http --vespa-url "$VESPA" --collection "$collection" \
        --space "$SPACE" --tei-url http://127.0.0.1:8081 --postgres-dsn "$DSN" --source "$collection" --fusion "$fusion" \
        --branches "$branches" --port 8090 > "$WORK/reports/service-$out.log" 2>&1 &
    local service=$!
    wait_for http://127.0.0.1:8090/health 60
    "$P" evaluation/load.py --url http://127.0.0.1:8090 --corpus manifests/corpus/litsearch.json \
        --queries manifests/queries/litsearch-test.json manifests/queries/litsearch-dev.json --data-dir "$WORK/data" \
        --out-dir "$WORK/reports/$out" --rates ${RATES:-10 20 40} --measured "${MEASURED:-5000}" \
        --repeats "${REPEATS:-3}" --cpus "$generator" --routes "$@" || true
    kill "$service"; wait "$service" 2>/dev/null || true
}

phase_b() {
    stage step3-msmarco env MSMARCO=1 bash infra/gpu/step3-embedding.sh
    stage step4-msmarco bash infra/gpu/step4-ann.sh
    # B_ENGINE=0: MS MARCO vectors, certified oracle and HNSW sweeps only, without the Vespa
    # projection of 8.8 M passages and the engine runs (decision 0007, reduced for budget).
    if [ "${B_ENGINE:-1}" = 0 ]; then
        echo "Phase B without the engine: MS MARCO projection, parity and runs skipped."
        return 0
    fi
    # The stack stages are shared with phase A; on a host that runs phase B alone they start it.
    stage stack stack_up
    stage migrate migrate
    stage vespa-deploy deploy_vespa
    stage project-msmarco project config/ann/msmarco-embeddinggemma-300m-fp32.toml \
        manifests/transform/passage-identity-v1.json msmarco
    stage parity-msmarco parity config/ann/msmarco-embeddinggemma-300m-fp32.toml msmarco 100
    local marco; marco="$(label msmarco)"
    if [ ! -f "$WORK/stages/declared-msmarco.done" ]; then
        declare_runs --name msmarco --collection "$marco" --corpus manifests/corpus/msmarco-v1-passage-text.json \
            --transform manifests/transform/passage-identity-v1.json --space "$SPACE" \
            --queries manifests/queries/msmarco-passage-dev-small.json --lexical-profile bm25-msmarco
        commit_declarations "Declare MS MARCO development runs of collection $marco"
        touch "$WORK/stages/declared-msmarco.done"
    fi
    if [ ! -f "$WORK/stages/msmarco-runs.done" ]; then
        bench_run config/runs/generated/msmarco-dev.bm25.toml > "$WORK/stages/msmarco-bm25.out"
        bench_run config/runs/generated/msmarco-dev.dense.toml > "$WORK/stages/msmarco-dense.out"
        for system in bm25 dense; do cp "$(cat "$WORK/stages/msmarco-$system.out")/manifest.json" \
            "$WORK/reports/msmarco-dev-$system-manifest.json"; done
        touch "$WORK/stages/msmarco-runs.done"
    fi
    # Vespa BM25 (k1 0.82, b 0.68) against the Anserini reference with the same parameters, per query.
    stage bm25-calibration sh -c "'$P' evaluation/paired_bootstrap.py --left '$(cat "$WORK/stages/msmarco-bm25.out" 2>/dev/null)/per-query.jsonl' \
        --left-ranker run --right evaluation/evidence/bench-msmarco-dev-anserini-bm25-tuned-1e74b30e140b/per-query.jsonl \
        --right-ranker run --metric RR@10 > '$WORK/reports/bm25-calibration.json'"
}

for phase in $PHASES; do
    case "$phase" in
        A) phase_a ;;
        B) phase_b ;;
        *) echo "Unknown phase $phase" >&2; exit 1 ;;
    esac
done
echo "Finished phases: $PHASES. Reports: $WORK/reports."
