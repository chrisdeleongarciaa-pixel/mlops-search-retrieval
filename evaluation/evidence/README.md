# Evidence index

The checkout links evidence to its experiment and commit. Release [v0.2](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/releases/tag/v0.2) identifies the original `checkpoint/2026-11-08` source tree. The paired audit and private-delivery results are later evidence.

| Record | Evidence | Scope |
| --- | --- | --- |
| Bibliographic and PDF checkpoint | [Hashes and outcomes](scientific-pdf-v1/evidence-index.json) | LitSearch inputs, SciFact reproduction, PDF extraction and rejected attempts. |
| SciFact exact scores | [Manifest](scifact-gemma-exact-cpu/manifest.json) · [Per-query scores](scifact-gemma-exact-cpu/per-query.json) | Exact E5 and Gemma, BM25 and RRF. |
| Paired E5/Gemma | [Audit](scifact-paired-42465181926/audit.json) · [Verification report](../../docs/verification/scifact-stability.md) | Eight indices, engine traces, independently recomputed rankings and paired intervals. |
| Early E5 HNSW stability | [Aggregate](scifact-stability-e5-42290872584/aggregate.json) | Four E5 insertion orders on the earlier host. |
| Mutable service | [Lexical](dense-witness-actions-42278239372/lx01/manifest.json) · [Dense](dense-witness-actions-42278239372/vector-service/dense-manifest.json) · [Hybrid](dense-witness-actions-42278239372/vector-service/hybrid-manifest.json) | Single-client retrieval timing with actual E5 encoding and eligibility reads. |
| Private delivery and MCP | [Run record](real-inference-42540081953/record.json) · [Coverage](../../docs/verification/integration-gate.md) | Real Qwen CPU answer, source bytes, ACL, endpoint failure and filesystem permissions. |

The verified Gemma vector bundle lives at commit `9dcdc5e23465d013b90bd1bb2420976fa7cbda12`, retained by the [fixed artifact tag](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/tree/artifacts/gemma-scifact-vectors-3c3af93c-fixed). Its SHA-256 is `3c3af93cb430a5a8de79051a46bb6da200254848c033ce7ea2f39ffe2986ac2b`. The workflow still takes the full commit SHA.
