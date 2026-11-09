# Project status

The source checkpoint is `checkpoint/2026-11-08`, also tagged `v0.2`. This page records current evidence on the default branch. Each verification claim applies to the profile and run identified in its record. [Configuration record](../engineering/checkpoints/2026-11-08.md) · [Evidence index](../evaluation/evidence/README.md)

| Capability | Verified profile | Open criterion |
| --- | --- | --- |
| Durable ingestion | v1 and v2 acceptance, stable receipts, transactional outbox, ordered checkpoint and real process cuts. | Real connector replay, cleanup and restore under representative backlog. |
| Publication and ACL | PostgreSQL/Vespa updates, withdrawal, late workers, cached context and scoped ACL tests. | Activate PDF generations and measure revocation in all target routes. |
| Scientific PDF | Pinned GROBID, 94 chunks from one scientific PDF, independently verified offsets, hashes and E5 token counts. | Annotated corpus, warmup and worker recovery after timeout. |
| English bibliography | LitSearch snapshot, 64,183 articles and disjoint 300/258 evaluation/development selections. | Rank LitSearch and evaluate Qwen and Gemma rewrite fidelity, quality and cost. |
| SciFact retrieval | BM25, exact, HNSW and RRF. RET-01 passes for E5 and Gemma in the registered engine profile. RET-02 passes for Gemma exact and HNSW on the fixed test set. | Fusion misses RET-02 for Gemma. Evaluate rank effects on development and transfer before changing the ranking policy. |
| Context and inference | Real Qwen CPU delivery on the private lexical witness, including citation provenance, failure and revocation. Isolated vLLM/L4 pilot. | Authenticated transport, representative streaming and workload, target runtime cancellation. |
| Release and CI | Automatic CPU gate and protected `main`, traceable checkpoint and evidence index. | Promotion and rollback of a compatible model, data, index and policy bundle in the target environment. |

## Gate disposition

- **RET-01** · Gemma meets recall@10 ≥0.95 and Vespa p95 <25 ms in all 12 HNSW build/cohort cells. The ≈1% cohort uses exact fallback. [Engine evidence](verification/scifact-stability.md#engine-acceptance)
- **RET-02** · Gemma exact and HNSW score 0.78871 NDCG@10. HNSW + BM25 scores 0.74264 against the 0.75 target. Query-level intervals are archived. [Quality and uncertainty](verification/scifact-stability.md#paired-uncertainty)
- **SRV-01** · The mutable E5 witness measured 42.10 ms lexical, 147.02 ms dense and 180.86 ms hybrid p95. The isolated L4 pilot exceeded 3 s end-to-end in all four load points. [Retrieval timing](verification/dense-retrieval.md) · [L4 pilot](verification/witness-increments.md#l4-inference-pilot)
- **ACL-01, SRV-02 and QA-01–QA-04** · Functional evidence exists for fencing, authorization and citations. Temporal revocation, availability, recovery and answer-quality acceptance require target-profile measurement. [Service objectives](../engineering/operations/service-objectives.md)

The next integration boundary joins the PDF generation to publication, retrieval and withdrawal. The retrieval research track retains separate protocols for transfer, reranking and representation learning. [Research program](research-program.md)
