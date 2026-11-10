# Scope and acceptance objectives v1

The target domain is English ML/NLP literature, with article relevance over titles and abstracts. LitSearch provides the frozen domain queries and inherited positives. SciFact is the independent regression benchmark. The planned rewrite comparison holds queries, schema, retriever and qrels constant for original, Qwen and Gemma. Report Recall@5/20, lost or altered constraints and time to validated JSON. [Rewrite protocol](../../config/rewrite-experiment.json)

The lab service load uses 8 and 16 concurrent users, zero inter-request pause, 20 results, 3 s deadlines, 100 warmup requests and 1,200 measured requests per route. Set open arrival rates after capacity is measured. Include rewrite time in retrieval when it blocks that path. [Load profile](../../config/workload-profile.json)

| Gate | Target | Required measurement |
| --- | --- | --- |
| RET-01 | ANN recall@10 ≥0.95 and engine p95 <25 ms at the same point | Hold corpus, metric, space, snapshot and eligibility fixed. Measure overlap against exact search, actual algorithm, returned depth, valid empties and filter selectivity. Record load, hardware and index size. Capacity requires sustainable QPS measurements. |
| RET-02 | SciFact NDCG@10 ≥0.75 | Pin qrels and evaluator and freeze parameters before test. Preserve BM25, dense, RRF and any reranker outputs and report Recall@10 and @100 separately. |
| SRV-01 | Full retrieval p95 <50 ms, TTFT p95 <1.5 s, E2E p95 <3 s, errors <0.5%, no observed fidelity loss at 8 and 16 clients | Measure from the client with stated mix, lengths, queue and offered rate. Count encoding, engine, authorization and any blocking rewrite. Report p50/p95/p99, throughput, saturation and last passing point. |
| SRV-02 | 99.9% over 30 days and recovery <120 s per declared fault | Count valid complete outcomes including valid empties. After injecting a fault, require three successive correct probes and verify revisions and ACL. Report each recovery, maximum and observed MTTR. |
| ACL-01 | Revocation effective in <1 s across search, cache, context and delivery | Time from authority commit to exclusion. Exercise the final authorization-to-emission race. Fail closed on unavailable authority or expired budget. |

The transport must bound authorization validity through release of a private body. Reauthorize when the delivery start can exceed that bound. Test faults, restoration, revocation and the path to the first emitted byte separately.

## Answer quality

Freeze at least 200 queries covering factual, navigational, complex semantic, multi-hop, ambiguous and unanswerable cases. Label expected answer behavior, documents and evidence units. Record each multi-hop unit and when to clarify. Report correct abstention separately from abstention on answerable queries.

| Gate | Target | Measurement |
| --- | --- | --- |
| QA-01 | Faithfulness ≥0.85 for scientific and ≥0.90 for a future high-risk profile | Mean supported-claim fraction per answer with effective context, claim-bearing count and answerable coverage. |
| QA-02 | Relevance ≥0.85 | Versioned rubric for answers, clarification and abstention, calibrated against human judgments. |
| QA-03 | Context recall ≥0.85 and precision ≥0.80 | Required evidence units entering the prompt and selected fragments contributing evidence. Report all-hop coverage. |
| QA-04 | Unsupported claim rate <2% | Aggregate unsupported claims over all emitted claims, with numerator, denominator and coverage. |

Select the target accelerator after a workload justifies it. Inventory CPU, RAM, disk, driver, CUDA and VRAM. Fix context length from actual prompts, reserved output and KV capacity. Record output tokens/s, TTFT, inter-token latency, E2E and free memory. Reserve 20–25% VRAM headroom and compare quantization against the full-precision baseline before adopting it.

Repeat affected correctness checks on ingestion or parser change. Use a fresh generation for a parser change and measure coverage, relevance and latency on the same cases. Keep exact, HNSW and fusion comparisons separate before reranking. Promote a compatible release only against evidence for its target profile. [Current gate status](../../docs/project-status.md)
