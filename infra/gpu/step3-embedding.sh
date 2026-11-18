#!/usr/bin/env bash
# Step 3 on a Linux GPU host: EmbeddingGemma vector sets for the reduced workbench scope.
#
#   HF_TOKEN=... bash infra/gpu/step3-embedding.sh   # conformance, SciFact, LitSearch, MTEB gate
#   MSMARCO=1 bash infra/gpu/step3-embedding.sh      # also MS MARCO passage (8.8 M passages, float32)
#
# Run from a clean checkout of the repository. Needs CPython 3.12, an NVIDIA GPU with a
# CUDA 13 driver (torch 2.14.0), about 10 GB of disk without MS MARCO and 50 GB with it.
# EmbeddingGemma is gated: accept its terms on Hugging Face and export HF_TOKEN.
# Every step is resumable; run the script again after an interruption.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo "The work tree must be clean: vector sets record the code commit." >&2
    exit 1
fi

WORK="${WORK:-$PWD/var}"
DEVICE="${DEVICE:-cuda}"
BATCH="${BATCH:-64}"
PYTHON="${PYTHON:-python3.12}"
SCIFACT_URL="https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/scifact.zip"
SCIFACT_SHA256="536e14446a0ba56ed1398ab1055f39fe852686ecad24a6306c80c490fa8e0165"
SPACE=embeddinggemma-300m-fp32
MODEL="$WORK/models/embeddinggemma-300m"
TITLE_FIELDS=manifests/transform/beir-title-text-fields-v1.json

mkdir -p "$WORK/data" "$WORK/models" "$WORK/vectors" "$WORK/reports"
if [ ! -x "$WORK/venv/bin/python" ]; then
    "$PYTHON" -m venv "$WORK/venv"
    "$WORK/venv/bin/python" -m pip install --require-hashes --only-binary=:all: \
        -r config/requirements-embed-linux-amd64-gpu.lock
    "$WORK/venv/bin/python" -m pip install --no-deps --no-build-isolation -e .
fi
P="$WORK/venv/bin/python"
"$P" -c "import torch; assert torch.cuda.is_available() or '$DEVICE' == 'cpu', 'No CUDA device'; print(torch.__version__, torch.version.cuda)"

if ! echo "$SCIFACT_SHA256  $WORK/data/scifact.zip" | sha256sum --check --status 2>/dev/null; then
    curl -fsSL -o "$WORK/data/scifact.zip" "$SCIFACT_URL"
    echo "$SCIFACT_SHA256  $WORK/data/scifact.zip" | sha256sum --check
fi

# Prints the directory of the vector set on its last line of JSON.
encode() {
    "$P" evaluation/embed.py encode --space "manifests/space/$SPACE.json" --model-dir "$MODEL" \
        --data-dir "$WORK/data" --vectors-dir "$WORK/vectors" --device "$DEVICE" --batch-size "$BATCH" "$@" \
        | tee /dev/stderr | tail -n 1 | "$P" -c "import json, sys; print(json.load(sys.stdin)['directory'])"
}

"$P" evaluation/fetch_model.py "manifests/space/$SPACE.json" "$MODEL"
# Adapter against the reference implementation, before any corpus is encoded.
"$P" evaluation/embed.py conformance --space "manifests/space/$SPACE.json" --model-dir "$MODEL" \
    --device "$DEVICE" | tee "$WORK/reports/conformance-$SPACE.json"

# LitSearch is prepared from its pinned parquet files; the outputs must match the archived hashes.
if [ ! -f "$WORK/data/litsearch/corpus.jsonl" ]; then
    "$P" evaluation/prepare_litsearch.py "$WORK/data/litsearch-source" "$WORK/data/litsearch" --download
fi

for collection in "beir-scifact:beir-scifact-test" "litsearch:litsearch-test litsearch-dev"; do
    corpus="manifests/corpus/${collection%%:*}.json"
    echo "== $SPACE on $corpus"
    "$P" evaluation/embed.py verify "$(encode --corpus "$corpus" --transform "$TITLE_FIELDS")"
    for queries in ${collection#*:}; do
        "$P" evaluation/embed.py verify "$(encode --corpus "$corpus" --transform "$TITLE_FIELDS" \
            --queries "manifests/queries/$queries.json")"
    done
done

# The published MTEB figure through the same adapter, with the official evaluator.
"$P" evaluation/mteb_gate.py embeddinggemma-300m --model-dir "$MODEL" --device "$DEVICE" --batch-size "$BATCH" \
    | tee "$WORK/reports/mteb-embeddinggemma-300m.json" || echo "MTEB gate failed" >&2

if [ "${MSMARCO:-0}" = "1" ]; then
    echo "== MS MARCO passage, float32"
    MSMARCO_ARGS="--corpus manifests/corpus/msmarco-v1-passage-text.json --transform manifests/transform/passage-identity-v1.json"
    "$P" evaluation/embed.py verify "$(encode $MSMARCO_ARGS --shard-rows 100000)"
    "$P" evaluation/embed.py verify "$(encode $MSMARCO_ARGS --queries manifests/queries/msmarco-passage-dev-small.json)"
fi

echo "Reports: $WORK/reports. Each vector set keeps manifest.json and encoding.json beside its shards."
