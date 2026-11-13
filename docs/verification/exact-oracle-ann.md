# Exact oracle and ANN sweep verification

Contract: [exact oracle and ANN sweeps](../contracts/exact-oracle-ann.md). Verified on CPython 3.12.10, Windows amd64, faiss-cpu 1.16.0, numpy 2.5.4, two cores and four threads. The GPU searcher and the MS MARCO gate are listed under [Not executed](#not-executed).

| Requirement | Evidence |
| --- | --- |
| Exact ranking | On 101 documents with three exact duplicates in different shards and IDs out of position order, the oracle equals an independent ranking by `math.fsum` with ID tie break, for the whole corpus and two cohorts. Duplicates tie and are ordered by ID. [Oracle tests](../../evaluation/test_oracle.py) |
| Certificate and escalation | Forty-one identical rows at the boundary force more candidates until every row is one; with a searcher limited to 20 candidates the query is scanned in full and gives the same list. |
| Fail closed | A searcher whose scores are off by 1e-3 stops the oracle with the error bound in the message. |
| float16 rows | float16 canonical rows searched through float32 give the independent ranking. |
| Cohorts | Buckets equal `sha256sum` over literal bytes; the threshold is strict; cohorts are nested and close to their fraction on 20,000 IDs. |
| Artifacts | A sealed oracle round-trips; a changed score fails verification. Quantized vector sets are refused as canonical rows. |
| Recall and frontier | Strict and tie-aware recall, quantiles, thresholds and the frontier equal hand calculations, including a cohort smaller than the cutoff and an empty one. [Sweep tests](../../evaluation/test_ann_sweep.py) |
| FAISS builds | With a beam over the whole corpus HNSW returns the exact list. Single-thread builds repeat their graph; another insertion order changes it. IVF-PQ repeats with its seeds and reports fewer bytes per vector. A result outside the cohort stops the sweep. A changed per-query file or `points.jsonl` marks the build incomplete. |
| Configuration | Each configuration rule, removed alone, fails a test. |

Twenty-three seeded faults in the oracle, cohorts, sweeps and configuration rules were each detected, after four tests that passed for the wrong reason were corrected. Two more mutants are equivalent: an unreachable cap in tie-aware recall, and a BLAS product that equals the numpy reduction on this machine.

## End-to-end run on SciFact with the synthetic space

Pipeline exercise only: 5,183 documents of the 64-dimension synthetic float16 space from step 3 and its 1,109 query rows. At this size exact search is cheap and HNSW is almost trivial; the numbers measure the code, not ANN quality. Code commit `9cdc1bd` (oracle definition and sweeps). [Evidence](../../evaluation/evidence/ann-smoke-synthetic-scifact/summary.json)

### Oracle

| Cohort (permille) | Eligible | States | ε | Largest observed error |
| --- | --- | --- | --- | --- |
| 1000 | 5,183 | 1,109 certified | 3.82e-6 | 6.56e-8 |
| 100 | 553 | 1,109 certified | 3.82e-6 | 6.56e-8 |
| 10 | 54 | 1,109 complete | 3.82e-6 | 4.91e-8 |
| 1 | 5 | 1,109 complete | 3.82e-6 | 3.86e-8 |

Each cohort was compared with a separate brute-force ranking (BLAS matrix product, then sort by score and ID): no query differs and the largest score difference is 0. A second execution reused all four oracles.

### Sweep

Seven builds and 76 settings in about three minutes. HNSW M 16, efConstruction 40, recall@10 (tie-aware):

| Order | Cohort | efSearch | Mean | 5th percentile | Share < 0.95 | Short results | Distances per query |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 11 | 1000 | 100 | 0.956 | 0.80 | 0.367 | 0 | 1,868 |
| 11 | 1000 | 400 | 0.9998 | 1.00 | 0.002 | 0 | 4,020 |
| 11 | 100 | 400 | 0.998 | 1.00 | 0.017 | 0 | 4,020 |
| 11 | 10 | 400 | 0.982 | 0.90 | 0.155 | 13,504 | 4,020 |
| 11 | 1 | 400 | 0.800 | 0.40 | 0.683 | 1,111 | 4,020 |
| 29 | 1000 | 400 | 0.9998 | 1.00 | 0.002 | 0 | 4,022 |
| 47 | 1000 | 400 | 0.9995 | 1.00 | 0.005 | 0 | 4,020 |

The full table, all settings and the frontiers are in the evidence summary. Three findings carry over to the MS MARCO run:

- **The mean hides the tail.** At efSearch 100 the mean is 0.956 while 37 % of queries are below 0.95. The step 4 gate reports the distribution for this reason.
- **FAISS filtering does not change the traversal.** Distances per query are the same in every cohort; the selector only decides which visited documents may enter the results. Recall at 0.1 % falls to 0.80 with efSearch 400, and the lists stay short although the cohort could fill them. This is FAISS's filtered HNSW, not the engine's strategy, which step 5 measures.
- **Builds repeated.** Two executions with four threads gave identical graph digests for all seven builds, and identical recall. At 8.8 M documents this is not assumed: the MS MARCO configuration builds one setting twice.

QPS on this laptop varied by up to a factor of two between runs of the same setting. It is not reported as a property of an index.

## Corrections made during verification

- **Short results.** The first version counted empty positions up to the search depth, including those a cohort smaller than the depth cannot fill. Now only positions within min(depth, eligible) count. FAISS was checked alone: with 5 of 800 documents eligible and efSearch 400 it leaves 3 eligible positions empty.
- **Stale reuse.** Finished builds were reused by configuration hash alone; a change of metric definition would have kept old results. Sweeps and oracles are now keyed by a digest of the measurement code as well.

## Not executed

Run after step 3 on a Linux GPU host. [`infra/gpu/run-steps-3-4.sh`](../../infra/gpu/run-steps-3-4.sh) runs both steps; [`infra/gpu/step4-ann.sh`](../../infra/gpu/step4-ann.sh) runs step 4 alone.

| Check | Status |
| --- | --- |
| GPU flat searcher (`GpuIndexFlatIP`, float16 storage, `use_cuvs = false`) | Not executed. Written against the FAISS 1.16.0 headers; faiss-cpu has no GPU classes. The observed-error check is the guard if the precision assumption is wrong. |
| Oracle over 8,841,823 MS MARCO passages, five cohorts | Not executed. |
| HNSW grid, three insertion orders and IVF-PQ on MS MARCO: the step 4 gate | Not executed. |
| SciFact oracles for E5, EmbeddingGemma and Qwen3 | Not executed: they need the step 3 vectors. |
| Linux lock with faiss-gpu-cuvs 1.16.0 | Resolved for `x86_64-manylinux_2_28` with hashes; never installed. It pins the CUDA 13.5 runtime. |
| Host memory and run time for MS MARCO | Not measured. Estimated from the index layout: 13.6 GB of float16 rows on the GPU for the oracle, up to about 18 GB of host memory per HNSW build. |
