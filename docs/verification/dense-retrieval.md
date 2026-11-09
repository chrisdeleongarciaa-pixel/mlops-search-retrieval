# Dense retrieval verification

| Requirement | Evidence |
| --- | --- |
| E5 FP32 conformance | Compare actual token IDs, masks and pooled vectors to the official reference. Record device, versions and hashes. [Conformance](../../evaluation/evidence/e5-colab-l4/observed.json) |
| Exact cosine and filtering | Hand-calculated unit vectors and exact Vespa comparison against the independent FP64 oracle by IDs, scores and cutoff. [Pilot audit](../../evaluation/evidence/scifact-dense-actions-42275419629/audit.json) |
| RET-01 and RET-02 | Freeze exploration on 809 development queries, keep engine and rank metrics on the 300 official test queries. [Paired verification](scifact-stability.md) |
| SRV-01 retrieval component | Match encoding, engine and eligibility measurements per request and attempt. Warm model with query and result caches disabled. |
| Withdrawal and coverage | Contract failures, one retry on eligibility change, and real PostgreSQL/Vespa withdrawal with a controlled encoder. [Integration gate](integration-gate.md) |

## Mutable service witness

Run [42278239372](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42278239372) used PostgreSQL 17.11 and Vespa 8.781.11 on two CPUs. Each route completed 120 requests with one client.

| Route | Retrieval p95 | Encoding p95 | Dense Vespa p95 | Lexical Vespa p95 |
| --- | ---: | ---: | ---: | ---: |
| BM25 | 42.10 ms | — | — | 14.04 ms |
| Dense | 147.02 ms | 104.84 ms | 14.41 ms | — |
| Hybrid | 180.86 ms | 133.99 ms | 14.02 ms | 6.84 ms |

The one-client lexical route meets the 50 ms retrieval target in this witness. Dense and hybrid exceed it. The paired timing samples preserve encoding and eligibility reads independently. [Dense manifest](../../evaluation/evidence/dense-witness-actions-42278239372/vector-service/dense-manifest.json) · [Hybrid manifest](../../evaluation/evidence/dense-witness-actions-42278239372/vector-service/hybrid-manifest.json)

## SciFact BEIR

Run [42275419629](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42275419629) used E5 FP32 on CPU. The encoder truncated 731 of 5,183 articles and zero development queries. Exploration zero reached 0.98900 ANN recall@10 on development. The test set showed 0.98967 full-corpus ANN recall@10 at 8.78 ms p95. The 30-query pilot at ≈10% measured 38.04 ms p95 under HNSW, and ≈1% used exact fallback at 6.48 ms p95. The four-build paired study uses a different filter representation and more observations. [Pilot manifest](../../evaluation/evidence/scifact-dense-actions-42275419629/manifest.json) · [Frozen parameters](../../evaluation/evidence/scifact-dense-actions-42275419629/freeze.json) · [Four-build audit](scifact-stability.md)
