# Experiment record

This record follows the evidence from protocol correctness to retrieval quality and delivery. The raw records retain their original hashes and configuration identities. [Evidence index](../evaluation/evidence/README.md)

| Experiment | Question | Result |
| --- | --- | --- |
| WP-01/LX-01 | Does the current publication survive an update and disappear after withdrawal, including a delayed worker? | Real PostgreSQL/Vespa witness passed. Lexical baseline measured 120 requests, 40 valid empty results and 64.43 ms retrieval p95 in the original run. [Verification](verification/witness-increments.md) |
| Mutable E5 | Does fragment coverage survive a mutable snapshot, and where is time spent? | Dense and hybrid withdrawal passed. In the measured profile, CPU query encoding accounted for much of the end-to-end cost. [Timing](verification/dense-retrieval.md#mutable-service-witness) |
| SciFact exact | How does representation change ranking with an exhaustive oracle? | NDCG@10 was 0.69390 for E5 and 0.78871 for Gemma. BM25 scored 0.66168. Gemma exact RRF raised Recall@100 from 0.975 to 0.980 and reduced NDCG@10 to 0.74246. [Manifest](../evaluation/evidence/scifact-gemma-exact-cpu/manifest.json) |
| Paired stability | Does HNSW retain neighbors and meet latency bounds across fresh builds? | Both encoders met the engine gate on the three HNSW cohorts. Gemma exact and ANN meet the SciFact quality target. Independent replay reproduced HNSW RRF at 0.74264. [Audit](verification/scifact-stability.md) |
| Bibliographic PDF | Can the original be accepted, extracted and chunked with independent provenance? | One eight-page PDF yielded 94 chunks and 27,064 canonical bytes. A cold request outlived the timeout and occupied the worker. [Verification](verification/scientific-pdf.md) |
| Real private delivery | Does a real model answer from eligible evidence and respect final revocation? | Qwen CPU returned a cited answer whose range resolved to the original. ACL and backend failure cases passed. [RI-01 and MCP-01](verification/integration-gate.md) |

The first paired dispatch, [42454352525](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42454352525), stopped before index construction because the bundle entry name in Git ended with CR. The fixed commit retained the same bytes under the expected filename. The subsequent run built both spaces on the same engine host. Encoding environments differed and are recorded separately. [Fixed artifact](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/tree/artifacts/gemma-scifact-vectors-3c3af93c-fixed) · [Successful paired run](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42465181926)

For retrieval errors, first inspect qrels, eligibility and coverage. A document recovered by exact search and missed by HNSW isolates approximation or filtering. An exact miss directs attention to encoding and representation. A loss at fusion directs attention to branch ranks and overlap. A document present among the first 100 but absent from the first ten is a candidate for a reranking diagnostic. Keep unjudged items separate from judged negatives.

The CUDA harness compares eager execution, `torch.compile` and explicit CUDA Graph replay in fixed token buckets. It retains parity, setup cost and memory measurements. GPU encoder and target load evidence remain separate from the archived Vespa measurements. [Runtime harness](../evaluation/query_encoder_cuda.py) · [Protocol](../engineering/decisions/0005-encoder-comparison.md)
