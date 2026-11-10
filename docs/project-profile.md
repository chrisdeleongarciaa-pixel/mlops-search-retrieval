# Scientific literature retrieval with evidence provenance

## Portfolio

I built a retrieval laboratory for English ML/NLP literature. PostgreSQL records idempotent acceptance, immutable revision identity and a transactional outbox. Vespa serves lexical and vector projections and publishes a new revision only after it is visible to search. Tests exercise withdrawal with an in-flight worker and prevent stale evidence from being reused.

The PDF pipeline uses GROBID and TEI to retain section, page coordinates, canonical byte ranges and hashes. An eight-page scientific PDF produced 94 verifiable chunks. In a separate end-to-end witness, a real Qwen model answered a query and cited an original byte range. The test rejected delivery after the underlying ACL grant was revoked. [PDF verification](verification/scientific-pdf.md) · [Real-model evidence](verification/integration-gate.md)

I evaluated BM25, exact cosine, HNSW and reciprocal rank fusion on 300 SciFact queries. EmbeddingGemma HNSW reached **0.78871 NDCG@10** versus **0.66168** for BM25. Across four HNSW builds, the full-corpus mean ANN recall@10 was **0.99908** at **9.44 ms Vespa p95** with one sequential client. Fusion raised Recall@100 and reduced NDCG@10. The paired bootstrap and saved query scores make that tradeoff reproducible. [Paired audit](verification/scifact-stability.md)

**Stack.** Python, PostgreSQL, Vespa, Docker Compose, GROBID, TEI, PyTorch, Transformers, Sentence Transformers, BEIR, llama.cpp, vLLM, MCP and GitHub Actions.

## CV

- Implemented idempotent ingestion with a transactional outbox and conditional Vespa publication. Verified continuity, ACL and withdrawal with PostgreSQL and Vespa integration tests.
- Benchmarked BM25, exact cosine, HNSW and RRF on 300 SciFact queries. Audited four fresh Gemma graphs and reproduced 0.78871 NDCG@10 for HNSW, with paired bootstrap intervals.
- Integrated provenance-preserving PDF extraction and tested private delivery with a real Qwen model. Built a separate MCP client with an allowlisted read tool and permission failure tests.

## LinkedIn

I built a scientific literature retrieval system to test what happens when a document changes while search and indexing are active. The pipeline combines durable acceptance in PostgreSQL, conditional publication in Vespa, and PDF chunks with verifiable page and byte provenance.

The SciFact study showed a concrete ranking tradeoff. EmbeddingGemma HNSW reached 0.78871 NDCG@10, while fusion with BM25 raised Recall@100 to 0.980 and lowered NDCG@10 to 0.74264. I archived the per-query scores and bootstrap procedure. The project also includes real-model private delivery and a filesystem MCP integration with explicit read permissions. [Project results](../README.md#results)
