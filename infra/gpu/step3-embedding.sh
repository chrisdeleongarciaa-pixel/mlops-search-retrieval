#!/usr/bin/env bash
# Vector-space gate on a Linux GPU host (rented instance or Colab terminal).
#
#   HF_TOKEN=... bash infra/gpu/step3-embedding.sh            # SciFact: conformance, shards, parity, MTEB
#   MSMARCO=1 bash infra/gpu/step3-embedding.sh               # also encode MS MARCO passage in float16
#
# Run from a clean checkout of the repository. Needs CPython 3.12, an NVIDIA GPU with a
# CUDA 13 driver (torch 2.14.0), about 15 GB of disk for SciFact and 40 GB with MS MARCO.
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

model_dir() {
    case "$1" in
        *e5*) echo "$WORK/models/multilingual-e5-base" ;;
        *gemma*) echo "$WORK/models/embeddinggemma-300m" ;;
        *qwen3*) echo "$WORK/models/qwen3-embedding-0.6b" ;;
    esac
}

# EmbeddingGemma takes the title in its own template slot; the others read title + space + text.
transform() {
    case "$1" in
        embeddinggemma-300m-fp*) echo "manifests/transform/beir-title-text-fields-v1.json" ;;
        *) echo "manifests/transform/beir-title-space-text-v1.json" ;;
    esac
}

# Prints the directory of the vector set on its last line of JSON.
encode() {
    "$P" evaluation/embed.py encode --space "manifests/space/$1.json" --model-dir "$(model_dir "$1")" \
        --data-dir "$WORK/data" --vectors-dir "$WORK/vectors" --device "$DEVICE" --batch-size "$BATCH" "${@:2}" \
        | tee /dev/stderr | tail -n 1 | "$P" -c "import json, sys; print(json.load(sys.stdin)['directory'])"
}

SCIFACT="--corpus manifests/corpus/beir-scifact.json"
for space in multilingual-e5-base-fp32 embeddinggemma-300m-fp32 embeddinggemma-300m-fp16 \
             qwen3-embedding-0.6b-fp32 qwen3-embedding-0.6b-fp16; do
    echo "== $space"
    "$P" evaluation/fetch_model.py "manifests/space/$space.json" "$(model_dir "$space")"
    # Adapter against the reference implementation, before any corpus is encoded.
    "$P" evaluation/embed.py conformance --space "manifests/space/$space.json" \
        --model-dir "$(model_dir "$space")" --device "$DEVICE" | tee "$WORK/reports/conformance-$space.json"
    documents="$(encode "$space" $SCIFACT --transform "$(transform "$space")")"
    queries="$(encode "$space" $SCIFACT --transform "$(transform "$space")" \
        --queries manifests/queries/beir-scifact-test.json)"
    "$P" evaluation/embed.py verify "$documents"
    "$P" evaluation/embed.py verify "$queries"
    case "$space" in
        *fp16) "$P" evaluation/embed.py parity "$documents" "$queries" | tee "$WORK/reports/parity-$space.json" ;;
        embeddinggemma-300m-fp32)
            # Compression axis: derived from the stored rows, never encoded again.
            "$P" evaluation/embed.py derive "$documents" --operation mrl:256 --vectors-dir "$WORK/vectors"
            "$P" evaluation/embed.py derive "$documents" --operation binary --vectors-dir "$WORK/vectors"
            ;;
    esac
done

# Published MTEB figures through the same adapter, with the official evaluator.
for check in multilingual-e5-base embeddinggemma-300m qwen3-embedding-0.6b; do
    echo "== MTEB $check"
    "$P" evaluation/mteb_gate.py "$check" --model-dir "$(model_dir "$check")" --device "$DEVICE" \
        --batch-size "$BATCH" | tee "$WORK/reports/mteb-$check.json" || echo "MTEB gate failed: $check" >&2
done

if [ "${MSMARCO:-0}" = "1" ]; then
    echo "== MS MARCO passage, float16"
    MSMARCO_ARGS="--corpus manifests/corpus/msmarco-v1-passage-text.json --transform manifests/transform/passage-identity-v1.json"
    documents="$(encode embeddinggemma-300m-fp16 $MSMARCO_ARGS --shard-rows 100000 --parity-sample 2000)"
    queries="$(encode embeddinggemma-300m-fp16 $MSMARCO_ARGS --queries manifests/queries/msmarco-passage-dev-small.json)"
    "$P" evaluation/embed.py verify "$documents"
    "$P" evaluation/embed.py parity "$documents" "$queries" | tee "$WORK/reports/parity-msmarco-embeddinggemma-300m-fp16.json"
fi

echo "Reports: $WORK/reports. Each vector set keeps manifest.json and encoding.json beside its shards."
