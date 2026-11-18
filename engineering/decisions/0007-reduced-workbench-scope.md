# 0007 · Reduced workbench scope

Decided by the project owner on 2026-11-17, to deploy a working retrieval system with measured results within a small GPU budget.

| Kept | Removed |
| --- | --- |
| EmbeddingGemma-300M in float32 as the only dense space. | Qwen3-Embedding and new E5 runs. The E5 space stays registered for its archived evidence. |
| MS MARCO passage dense embeddings, oracle and three HNSW insertion orders. | float16 storage, MRL truncation, binary and int8 forms as pipeline steps. The store still supports them. |
| HNSW with M = 16 and efConstruction = 200, the engine's setting. | The M × efConstruction grid and IVF-PQ. |
| LitSearch as the product corpus; SciFact as regression. | SciDocs, TREC-COVID, BRIGHT and MS MARCO v2.1. |
| Single active generation with compare-and-set publication. | Plural generations, aliases, shadow reads and the extended TLA+ model. |
| ACL tokens in the engine and ACL-01 measured. | — |
| TEI for online queries and SRV-01 with 5,000 samples per point, open arrivals and stage breakdown. | — |
| GROBID for PDFs. | The MinerU and Docling comparator. |
| RRF against convex fusion, chosen on LitSearch dev and reported once on test. | SPLADE and ColBERT. |

## Consequences

- **Storage.** MS MARCO is stored in float32 because Vespa has no float16 cells: 27 GB of vectors. The oracle indexes the GPU in blocks of rows so a 24 GB card suffices.
- **Precision.** float16 parity is no longer a gate. The open question of near ties under float16 is closed by removing float16.
- **Power.** LitSearch has 300 test queries, enough to detect about 0.05 of NDCG@10. A smaller fusion gain is reported as not detectable.

## Reopen when

- A second space or a compression study is required.
- More than one generation must serve at once, for example for a canary.
