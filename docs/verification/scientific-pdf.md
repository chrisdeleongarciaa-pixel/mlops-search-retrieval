# Bibliographic and PDF verification

| Requirement | Evidence and result |
| --- | --- |
| BIB-01, BIB-02 | Seven source files passed size and SHA-256 verification. Materialized 64,183 articles, 300 test queries and 258 development queries, with 39 related queries held out. Development and test share neither IDs nor positive articles. |
| BIB-03 | Manual oracle gives Recall@5 = 0.5 and Recall@20 = 1. Duplicate or incomplete candidate coverage is rejected. LitSearch ranking is pending. |
| RW-01, RW-02 | Two contract cases cover malformed or incomplete JSON, duplicate fields, altered `keep` and lost literals. |
| RW-03, RW-04 | Configuration exists. The comparator checkpoint, model runs and semantic judgments remain open. |
| SciFact reproduction | Pinned BEIR reproduced BM25 on all 300 queries with NDCG@10 0.66168, Recall@10 0.79094 and Recall@100 0.88589. The saved rankings were recalculated independently of regenerating BM25. |
| PDF-01–PDF-05 | Contract coverage includes bytes, format, corruption, encrypted PDFs, incomplete or unsafe TEI, page bounds, deterministic identity, missing locations and token limits. The PostgreSQL/GROBID witness passed. |

The integration accepted a 173,406-byte, eight-page scientific PDF and confirmed one outbox event with an independent SQL connection. The parser produced **94 chunks and 27,064 canonical bytes**. The byte verifier checked ranges and hashes, and every chunk had a locator. Independent E5 token counts ranged from 6 to 276 against the 512-token limit. The run used PostgreSQL 17.11, GROBID 0.9.1 CRF, Transformers 5.20.2 and tokenizers 0.23.3. Its wall time was 143.990 s and the warmed server extraction took 33.043 s. [PDF source and hash](../../evaluation/evidence/scientific-pdf-v1/pdf-source.json) · [Manifest](../../evaluation/evidence/scientific-pdf-v1/pdf-manifest.json) · [Integration record](../../evaluation/evidence/scientific-pdf-v1/pdf-acceptance-verification.json)

The version endpoint returns JSON. The initial client expected plain text, and a regression test covers the correction. A cold extraction exceeded the 90 s client timeout, completed at the server in 411.834 s, and occupied the worker when a later request returned HTTP 503. Neither failed attempt produced a publishable manifest. Memory pressure at the original 2 GiB limit motivated a 2.5 GiB container cap and 4 GiB memory-plus-swap cap. Warmup, worker recovery and annotated extraction quality remain separate acceptance work. The real run used E5 token counts. [Outcomes](../../evaluation/evidence/scientific-pdf-v1/pdf-access-outcomes.log) · [Procedure](../../engineering/operations/scientific-pdf.md)

The original local evidence records 7 unit tests, 26 contract tests, 3 materializer cases and one real PDF integration. Six PDF contracts also passed with `python -O`. The current CPU integration gate has its own coverage and run IDs. [Evidence index](../../evaluation/evidence/scientific-pdf-v1/evidence-index.json) · [Integration gate](integration-gate.md)
