# 0005 · Encoder comparison and HNSW stability

Use `intfloat/multilingual-e5-base` as the control and `google/embeddinggemma-300m` as the candidate. Identify each space by revision, hashes, prompt, token limit, pooling, normalization, dimension and stored type. Both use FP32 and normalized 768-component `float32` vectors with cosine and 512 effective tokens. E5 concatenates fields and prefixes retrieval roles. Gemma uses its published structured retrieval prompts. Keep truncation IDs by model.

Conformance uses each model's own reference. Require equal token IDs and masks, component error ≤1e-5 and cosine ≥0.99999. Verify exact Vespa parity at 10 and 100 against each space's independent exhaustive cosine oracle.

Freeze exploration at zero and `totalTargetHits=hits=100` on development. Use the official 300 BEIR test queries and 5,183 documents. Apply four SHA-256 insertion permutations on fresh indices. The four cohorts use the full generation and approximately 50%, 10% and 1% eligibility. Each build has 300 observations per cohort. Classify effective algorithms from matched traces and retain p95, recall, depth and coverage. A RET-01 p95 requires at least 100 observations of the algorithm per build. Host matching applies to engine deltas. Encoding and service latency remain separate. [Protocol](../../config/scifact-stability-v1.json) · [Audited run](../../docs/verification/scifact-stability.md)

The earlier 10% pilot used a different filter representation. A causal filter-cost comparison requires the same graph and eligible IDs with only the filter representation changed. The current study uses an indexed numeric attribute.

## Encoder runtime

Compare eager GPU, `torch.compile` and explicit CUDA Graph replay in single-query buckets of 32, 64, 128, 256 and 512 tokens. Count tokenization and transfer in the encoder budget. Record compilation, capture, allocated and reserved memory separately. Require vector and top-10 parity with eager. The 50 ms service criterion includes authority, retrieval and revalidation. Evaluate dynamic batching with its own queue and load. [CUDA harness](../../evaluation/query_encoder_cuda.py)
