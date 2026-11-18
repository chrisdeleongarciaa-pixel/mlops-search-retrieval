# Exact oracle and ANN sweeps

The exact oracle defines the truth of every ANN measurement. FAISS measures; the engine of step 5 is compared against the same oracle. [Verification](../verification/exact-oracle-ann.md) · [Oracle](../../evaluation/oracle.py) · [Sweeps](../../evaluation/ann_sweep.py) · [CLI](../../evaluation/ann.py) · [Vector spaces](vector-spaces.md)

## Cohorts

A document belongs to the cohort of `p` permille when `sha256(b"cohort\x00" + utf8(id))`, read as a big-endian integer from its first 8 bytes, is below `p` modulo 1000. Cohorts depend on the ID alone, so they are nested (0.1 % ⊂ 1 % ⊂ 10 % ⊂ 50 % ⊂ 100 %) and every component that filters selects the same documents. [Definition](../../src/search_retrieval/contracts/cohorts.py)

The earlier `study_bucket` attribute of the SciFact stability study (1 to 100) is a separate definition and is not used here.

## Oracle

| Element | Definition |
| --- | --- |
| Exact score | float64 sum of the products of the canonical query and document rows, reduced over the dimension axis by numpy for each pair. Identical rows score identically wherever they are stored. |
| Exact top k | Eligible documents by descending exact score; equal scores by document ID in code point order. Fewer than k rows when the cohort is smaller, padded with -1 and -inf. |
| Candidates | A FAISS flat inner-product index over the eligible rows, on CPU or GPU. float16 rows are stored as float16 on the GPU. |
| Certificate | With float32 accumulation over inputs float32 represents exactly, an approximate score is within ε = γ_d · max‖q‖ · max‖x‖ of the dot product, γ_d = d·u / (1 − d·u), u = 2⁻²⁴. When the k-th exact score exceeds the lowest candidate's approximate score by ε plus the float64 rounding of both sides, no document left out can enter the top k, ties included. |
| Escalation | A query that does not clear the certificate is searched with four times the candidates. Past the searcher's limit (all rows on CPU, 2,048 on GPU) its eligible documents are scored exactly. |
| Fail closed | The largest difference between approximate and exact scores over every candidate must not exceed ε. Otherwise the searcher does not accumulate in float32 and the oracle stops. `NVIDIA_TF32_OVERRIDE` is refused. |

FAISS 1.16 multiplies float16 rows with `cublasGemmEx` in `CUBLAS_COMPUTE_32F_PEDANTIC` and float32 rows with SGEMM under `CUBLAS_MATH_DISALLOW_REDUCED_PRECISION_REDUCTION`, and the classic GPU path is used (`use_cuvs = false`). Read in the FAISS source at tag `v1.16.0`: `faiss/gpu/utils/MatrixMult-inl.cuh`, `faiss/gpu/StandardGpuResources.cpp`, `faiss/gpu/GpuIndex.h`.

### Artifacts

Each cohort writes `rows.npy`, `scores.npy` and a sealed `manifest.json` (`kind = exact-oracle`) naming the document and query vector sets, the space, k, the cohort, the score and tie-break definitions and the hashes of both arrays. The body does not depend on the device or searcher. `observation.json` records how it was computed: code commit, device, ε, the observed maximum error, how many queries were complete, certified or fully scanned, and timings. An oracle already written for the same inputs is reused.

## Sweeps

| Element | Definition |
| --- | --- |
| HNSW | `IndexHNSWSQ` with float16 storage for float16 spaces (exact), `IndexHNSWFlat` for float32, inner product. Parameters M and efConstruction; searched with efSearch. |
| IVF-PQ | `IndexIVFPQ`, inner product, coarse k-means and PQ seeded by `train_seed`, trained on a sample drawn by that seed. Searched with nprobe. |
| Build identity | Family, parameters, insertion order (a permutation drawn from the insertion seed) and add batch. FAISS inserts the vectors of each add call level by level, so another batch gives another graph. |
| Filtering | A FAISS bitmap selector over the cohort, applied during the search, before the top k. A result outside the cohort stops the sweep. |
| Recall per query | m = size of the exact top k at the cutoff. Strict: share of the exact top m among the first m results. Tie-aware: results whose exact score is at least the m-th exact score, capped at m. Summaries use tie-aware recall. Queries with an empty cohort are excluded and counted. |
| Per setting | Mean and quantiles (0, 1, 5, 10, 50 %) of recall at 10 and 100; share of queries below 0.95 and 0.99; QPS from the median of repeated runs after warmup; distances computed per query (FAISS counters); results missing from short lists. |
| Per build | Build seconds, threads, estimated index bytes per vector (rows or codes plus graph or lists, allocator overhead excluded) and a SHA-256 of the graph or of the inverted lists and centroids. |
| Frontier | Settings not dominated in (mean tie-aware recall, QPS), per cohort and cutoff, across builds. |

Each build directory holds per-query recall arrays, `points.jsonl` and, written last, `build.json` with their hashes. A build whose files no longer match is redone; finished builds are skipped. `summary.json` gathers the frontiers, structure digests and bytes per vector with the code commit, configuration hash and oracle hashes.

## Configurations

| File | Purpose |
| --- | --- |
| [`smoke-synthetic-scifact.toml`](../../config/ann/smoke-synthetic-scifact.toml) | Pipeline exercise on the synthetic space. |
| [`scifact-embeddinggemma-300m-fp32.toml`](../../config/ann/scifact-embeddinggemma-300m-fp32.toml) | Oracle of SciFact, used by ACL-01. |
| [`litsearch-embeddinggemma-300m-fp32.toml`](../../config/ann/litsearch-embeddinggemma-300m-fp32.toml) | Oracle of LitSearch for the engine parity check; HNSW M 16, efConstruction 200, three insertion orders. |
| [`msmarco-embeddinggemma-300m-fp32.toml`](../../config/ann/msmarco-embeddinggemma-300m-fp32.toml) | The step 4 gate: oracle over 8,841,823 passages in blocks of rows, five cohorts, three insertion orders. |

The M × efConstruction grid and IVF-PQ were removed by [decision 0007](../../engineering/decisions/0007-reduced-workbench-scope.md). For corpora larger than device memory, `ann.py oracle --block-rows` indexes one block at a time and merges the best candidates of each block; the certificate holds because a document left out scores at most the k-th candidate of its block.

## Limits

- The search depth is the largest recall cutoff, so efSearch below 100 is not measured.
- Timings depend on the host and its load. QPS is a property of one run, not of the index.
- Build reproducibility under multithreading is observed, not assumed: the MS MARCO configuration builds M 16 / efConstruction 200 / seed 11 twice and records both digests.
- The float64 reduction is deterministic for one numpy and CPU; another BLAS or numpy could break a near tie differently. Exact duplicates still tie.
- Selectivity strategies of the engine (post-filter, pre-filter, ACORN-1, exact) belong to step 5. FAISS filtered HNSW only shows how one filtered graph search degrades.
