#!/usr/bin/env bash
# Steps 3 and 4 in one session on a Linux GPU host:
#   HF_TOKEN=... bash infra/gpu/run-steps-3-4.sh
# Step 3 encodes SciFact for every workbench space and MS MARCO passage in float16; step 4 builds
# the exact oracles and runs the ANN sweeps. Both steps resume after an interruption.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
MSMARCO=1 bash infra/gpu/step3-embedding.sh
bash infra/gpu/step4-ann.sh
