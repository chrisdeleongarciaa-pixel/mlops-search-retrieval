#!/usr/bin/env bash
# Step 4 on a Linux GPU host: exact oracle on the GPU, ANN sweeps on the CPU.
#
#   bash infra/gpu/step4-ann.sh                 # SciFact for three spaces, then MS MARCO
#   SCIFACT_ONLY=1 bash infra/gpu/step4-ann.sh  # skip MS MARCO
#
# Run after infra/gpu/step3-embedding.sh, from the same clean checkout and with the same WORK.
# MS MARCO needs the float16 vectors written by `MSMARCO=1 bash infra/gpu/step3-embedding.sh`.
# The MS MARCO oracle keeps 13.6 GB of float16 rows on the GPU; the sweeps hold one index at a
# time in host memory, up to about 18 GB for M = 48, plus page cache for the vector shards.
# Every oracle cohort and every build is kept once finished; run the script again to resume.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo "The work tree must be clean: oracles and sweeps record the code commit." >&2
    exit 1
fi
if [ "${NVIDIA_TF32_OVERRIDE:-0}" != "0" ]; then
    echo "Unset NVIDIA_TF32_OVERRIDE: the oracle's error bound assumes float32 accumulation." >&2
    exit 1
fi

WORK="${WORK:-$PWD/var}"
DEVICE="${DEVICE:-cuda}"
PYTHON="${PYTHON:-python3.12}"
OUT="$WORK/ann"
mkdir -p "$OUT" "$WORK/reports"

if [ ! -x "$WORK/venv-ann/bin/python" ]; then
    "$PYTHON" -m venv "$WORK/venv-ann"
    "$WORK/venv-ann/bin/python" -m pip install --require-hashes --only-binary=:all: \
        -r config/requirements-ann-linux-amd64-gpu.lock
    "$WORK/venv-ann/bin/python" -m pip install --no-deps --no-build-isolation -e .
fi
P="$WORK/venv-ann/bin/python"
"$P" -c "import faiss, sys; n = faiss.get_num_gpus(); print('faiss', faiss.__version__, 'GPUs', n); sys.exit(0 if n or '$DEVICE' == 'cpu' else 1)"
echo "CPU threads: $(nproc)"; free -g | head -2

run() {
    local config="$1" name
    name="$(basename "$config" .toml)"
    echo "== oracle $name"
    "$P" evaluation/ann.py oracle "$config" --vectors-dir "$WORK/vectors" --out-dir "$OUT" --device "$DEVICE" \
        | tee "$WORK/reports/oracle-$name.jsonl"
    echo "== sweep $name"
    "$P" evaluation/ann.py sweep "$config" --vectors-dir "$WORK/vectors" --out-dir "$OUT" \
        | tee "$WORK/reports/sweep-$name.jsonl"
}

for space in multilingual-e5-base-fp32 embeddinggemma-300m-fp32 qwen3-embedding-0.6b-fp32; do
    run "config/ann/scifact-$space.toml"
done

if [ "${SCIFACT_ONLY:-0}" != "1" ]; then
    run config/ann/msmarco-embeddinggemma-300m-fp16.toml
fi

# One summary per sweep: frontiers, structure digests and bytes per vector.
find "$OUT" -name summary.json -path '*sweep-*' -exec cp --parents {} "$WORK/reports/" \;
echo "Reports: $WORK/reports. Oracles and sweeps: $OUT."
