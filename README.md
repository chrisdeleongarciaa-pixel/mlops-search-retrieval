# Scientific literature retrieval with verifiable evidence

This project searches scientific literature with BM25, exact cosine retrieval, and HNSW in Vespa. PostgreSQL owns publication and access state. A search index can be rebuilt from versioned inputs, and a citation can be resolved to the bytes of its source. The project includes reproducible SciFact benchmarks, scientific PDF extraction, and a private-delivery test with a real model.

## Results

All quality scores below use the 300 official BEIR SciFact test queries. The HNSW and fusion rows are means across four fresh insertion orders on the same Vespa host. BM25 and dense lists were fused at depth 100 with equal-weight RRF and constant 60. The audit rebuilt both fusions from the recorded branches and recomputed their scores with the pinned evaluator. [Paired-run audit](docs/verification/scifact-stability.md) · [Per-build evidence](evaluation/evidence/scifact-paired-42465181926/audit.json)

| Ranking | NDCG@10 | Recall@10 | Recall@100 |
| --- | ---: | ---: | ---: |
| BM25 | 0.66168 | 0.79094 | 0.88589 |
| E5 exact | 0.69390 | 0.80722 | 0.93933 |
| E5 HNSW | 0.69190 | 0.80639 | 0.94683 |
| E5 exact + BM25 | 0.70202 | 0.82722 | 0.95767 |
| E5 HNSW + BM25 | 0.70161 | 0.82722 | 0.95933 |
| EmbeddingGemma exact | **0.78871** | **0.91856** | 0.97500 |
| EmbeddingGemma HNSW | **0.78871** | **0.91856** | 0.97500 |
| EmbeddingGemma exact + BM25 | 0.74246 | 0.85622 | **0.98000** |
| EmbeddingGemma HNSW + BM25 | 0.74264 | 0.85622 | **0.98000** |

Gemma HNSW meets the **0.75 NDCG@10** target for RET-02 on this test set. Fusion finds more relevant documents by rank 100 and scores below the target at rank 10. The paired bootstrap reports query-level intervals for exact and HNSW comparisons. [Bootstrap and decision record](docs/verification/scifact-stability.md#paired-uncertainty)

| EmbeddingGemma cohort | Effective algorithm | ANN recall@10 | Vespa p95 |
| --- | --- | ---: | ---: |
| Full corpus | HNSW | 0.99908 | 9.44 ms |
| ≈50% eligible | HNSW | 0.99958 | 9.07 ms |
| ≈10% eligible | HNSW | 1.00000 | 7.24 ms |
| ≈1% eligible | Exact by selectivity | 1.00000 | 5.41 ms |

Each row summarizes four builds of 300 queries with one sequential client. All three HNSW cohorts passed RET-01 on every build. The last row records exact fallback. Engine latency starts with a precomputed query vector. [Per-build measurements and limits](docs/verification/scifact-stability.md#engine-acceptance)

## System and verification

| Stage | Verified scope |
| --- | --- |
| Ingestion and publication | Idempotent acceptance and transactional outbox in PostgreSQL. Vespa activation is conditional on the current revision. Tests exercise updates, withdrawal, late workers and ACL. [Contracts](docs/contracts/witness-increments.md) · [Verification](docs/verification/witness-increments.md) |
| Scientific PDFs | GROBID and TEI produce chunks with page coordinates and verifiable byte spans. An eight-page sample yielded 94 chunks. [PDF evidence](docs/verification/scientific-pdf.md) |
| Bibliographic corpus | LitSearch materialization contains 64,183 articles and a frozen evaluation set of 300 queries. Ranking and rewrite results on LitSearch remain open. [Corpus contract](docs/contracts/bibliographic-profile.md) |
| Inference and delivery | The private CPU witness used Qwen2.5-1.5B and llama.cpp. It resolved a generated citation to the accepted original and rejected unauthorized or revoked delivery. [RI-01 evidence](docs/verification/integration-gate.md) |
| MCP client | A model API calls the upstream filesystem server through one allowed read tool. The example tests rejected tools, aliases and paths. [MCP example](examples/mcp-client/) |

The private witness uses a synthetic UTF-8 document and a principal supplied by the harness. Authenticated transport, PDF generation activation, and service behavior under representative load have separate acceptance criteria. [Project status](docs/project-status.md)

## Reproduce

Python 3.12 or later is required. Run from the repository root.

```sh
python -m pip install -e ".[pdf]"
python -m unittest discover -s tests/unit -v
python -m unittest discover -s tests/contract -v
```

- [Architecture and state ownership](docs/architecture.md)
- [Evidence index](evaluation/evidence/README.md)
- [Integration gate and real-model procedure](docs/verification/integration-gate.md)
- [Engineering runbooks](engineering/operations/README.md)
- [v0.2 checkpoint release](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/releases/tag/v0.2)

## License

Project code and authored documentation are available under [Apache License 2.0](LICENSE). Referenced models, datasets, external samples and upstream components retain their respective terms.
