# vLLM serving pilot

Record model, tokenizer, template, backend, driver and runtime in the serving manifest. The gateway owns budgets and release policy. Integration tests should observe cancellation and resource release at the model process.

## Colab inventory

Run `colab_preflight.py` inside the Linux runtime and retain the JSON. The probe reads CPU, RAM, disk, GPU, driver, package versions and cgroup v2 limits. It installs no packages and downloads no weights. Check available storage for model files, cache and runtime. A Docker CLI entry establishes CLI presence.

## L4 capacity pilot

Verify all Qwen files against the [model manifest](../../../config/model-sources.json). The observed pilot used vLLM 0.33.0 on CUDA 13.0. Create an isolated environment with `python -m venv --without-pip venv` and install with `uv pip install --python venv/bin/python --torch-backend=cu130 --only-binary=:all: vllm==0.33.0`. Preserve the resolved environment with `uv pip freeze --python venv/bin/python`.

Bind to `127.0.0.1`, use BF16, context 4,096 and eight sequences. Set `--gpu-memory-utilization 0.77`, `--max-num-batched-tokens 2048`, `--enable-chunked-prefill`, `--no-enable-prefix-caching`, `--enforce-eager` and `--generation-config vllm`. The observed run used `VLLM_USE_FLASHINFER_SAMPLER=0` after the FlashInfer warmup required Ninja.

Check `/health` and a known-answer chat. For `vllm bench serve` use random data with seed 47, one warmup, `--ignore-eos`, `--request-rate inf` and `--random-range-ratio 0`. Measure sixteen requests of 1,024 input and 96 output tokens at each of concurrency 1, 4 and 8. For the long case use eight concurrent requests of 3,800 input and 256 output tokens. Record per-request TTFT, TPOT, ITL and E2E and sample VRAM every 500 ms. [Observed pilot](../../../docs/verification/witness-increments.md#l4-inference-pilot) · [Benchmark CLI](https://docs.vllm.ai/en/latest/cli/bench/serve/)
