# Engine projection, serving, ACL and fusion verification

Contract: [engine projection, serving, ACL and fusion](../contracts/serving.md). The table below was verified on CPython 3.12.10, Windows amd64, with HTTP fakes of Vespa and TEI; the next section gives the run against Vespa, TEI and PostgreSQL on a GPU host.

| Requirement | Evidence |
| --- | --- |
| Feed and read back | Feeding retries overload responses (429) and stops on other errors; vectors read back equal the float32 rows exactly. [Client tests](../../evaluation/test_vespa_passages.py) |
| Filters | Collection, cohort and ACL token filters select exactly the expected documents; revoking a token removes the document from filtered results. |
| BM25 grammar | The query sends `{grammar: "any", defaultIndex: "text"}userInput(@q)`. |
| Exact parity rule | Identical lists agree; a swap at a tie within 1e-5 agrees; a missing neighbour, a score gap or a short list disagrees. |
| Engine runs | `vespa-bm25`, `vespa-dense` and `vespa-hybrid` produce the lists of the fusion functions applied to the engine's branches; inconsistent configurations are rejected. |
| Fusion | Min-max scaling and convex combination equal hand calculations; alpha 0 and 1 reproduce each branch's order. The choice rule and the promotion gate behave as specified. [Fusion tests](../../evaluation/test_fusion_sweep.py) |
| Service | Each route reports its stages; a private document needs a grant; a revocation at the authority excludes it at delivery before the engine update; an unreachable authority fails the request. The HTTP front end answers 200, 400 and 503 as specified. [Service tests](../../evaluation/test_serving.py) |
| TEI client | The rendered prompt is sent; non-unit, mis-shaped and failed responses are rejected. |
| Open-loop load | With one worker and a 0.3 s stall, later requests' latency includes their wait from the scheduled time, and the generator's send lag exposes it. |
| Percentile intervals | The ranks equal the workbench table for 300, 1,200, 5,000 and 10,000 samples; scipy's binomial gives each at least 95 % coverage. |
| ACL-01 measurement | A revocation is excluded at delivery without leaks; an authority that does not revoke, with an engine update 0.2 s late, leaks and is counted. |
| Declarations | Generated run configurations resolve; the Linux runtime manifest pins CPython 3.12.10 and the GPU lock. |

## Executed on a GPU

Phase A of [`run-all.sh`](../../infra/gpu/run-all.sh) at commit `1f8af90`, 2026-11-18, on a vast.ai VM: RTX 3090 (compute capability 8.6, driver 580.178.04, CUDA 13.0), 30 vCPU, 49 GB of RAM, Ubuntu 22.04 with kernel 5.15.0-1067-kvm, Vespa 8.781.11, TEI 1.9.4 (`86-1.9.4`), PostgreSQL from the compose file. The pipeline took 1.96 h. [Evidence](../../evaluation/evidence/gpu-litsearch-rtx3090-2026-11-18)

| Check | Result | Verdict |
| --- | --- | --- |
| `passage` schema on Vespa 8.781.11 | Deployed, including `filter-first-threshold`. | SATISFIED |
| Projections | SciFact (5,183) and LitSearch (64,183) fed; counts and read-back samples equal the canonical rows. | SATISFIED |
| Step 5 parity | `approximate:false` equals the oracle's top 100 for all 300 test queries in the five cohorts; largest score gap 2.1e-7 (tolerance 1e-5). | SATISFIED |
| ANN in the engine | Mean recall@10 against the oracle at explore 0 / 100 / 300: 0.982 / 0.992 / 0.998 over all documents; 0.989 / 0.998 / 0.999 at 50 %; 0.989 / 0.997 / 0.999 at 10 %; 1.0 at 1 % and 0.1 %, where the engine switches to exact search. | Reported |
| Fusion choice on development | Convex, alpha 0.55: nDCG@10 0.547 against 0.481 dense and 0.460 BM25 over 258 queries; paired 95 % interval of the gain over dense [0.037, 0.095]. | SATISFIED |
| Test report, once | nDCG@10 on 300 queries: BM25 0.406, dense 0.459, fused 0.524. R@100: 0.766, 0.861, 0.881. | Reported |
| Promotion on test | Fused minus dense +0.065, paired 95 % interval [0.038, 0.094]. | SATISFIED |
| TEI parity | Cosine ≥ 0.9999995 on all 558 queries; maximum absolute difference 1.3e-4 against the 1e-5 limit, exceeded by every query. The difference is systematic; its effect on rankings is not measured. | NOT SATISFIED |
| ACL-01 | 683 revocations measured, 317 skipped because the principal did not retrieve the document first; 0 leaks; delivery exclusion p99 9.1 ms [8.6, 10.7], engine exclusion p99 15.6 ms. | NOT SATISFIED (683 of the 1,000 required) |
| SRV-01 | 5,000 samples per point, 3 repeats, generator send lag p99 0.33 ms. p95 in ms: dense 37–41 at 10 q/s, 38–48 at 20 q/s, 44–66 at 40 q/s; BM25 53–57 at 10 q/s; fused 86–92 at 10 q/s. The last rate with p95 under 50 ms in every repeat is 20 q/s for dense; none for BM25 and fused. | Dense SATISFIED to 20 q/s; BM25 and fused NOT SATISFIED |

Phase B without the engine at commit `51d6a25`, 2026-11-19, on an RTX 4090 VM (23 vCPU, 49 GB): the 8,841,823 MS MARCO passages were encoded in float32 at about 1,100 passages per second and the set verified; conformance (2.4e-7) and the MTEB figure (0.78675) passed on that GPU as well. [Evidence](../../evaluation/evidence/gpu-msmarco-encoding-rtx4090-2026-11-19)

## Not executed

| Check | Status |
| --- | --- |
| MS MARCO projection, engine parity and runs; Vespa BM25 against Anserini | Not executed. |
| MS MARCO oracle and HNSW sweeps | Not executed. The first attempt stopped on a GPU memory error, now corrected (see the oracle verification). |
| PostgreSQL integration tests | Not executed locally; the authority ran in ACL-01 on the GPU host. |

## Corrections made during verification

- **Deadline failing open.** The watchdog's deadline was computed with `python3`; where it is missing the deadline was empty and every comparison failed. It is now computed with `awk` and an invalid deadline stops the watchdog at start, which the launch check shows.
- **Watched process.** The launch recorded `$!` of `setsid`, which forks when started from an interactive shell (util-linux behaviour, not observed here), so the watchdog would have seen the pipeline end at once. `run-all.sh` now records its own process ID.
- **Queries without results in the fusion sweep (found on the GPU).** `run.trec` has no line for a query without hits, so BM25 seemed to answer fewer development queries than dense. Each branch now lists every query of its per-query records.
- **MTEB similarity (found on the GPU).** mteb 2.27.12 compares the similarity by identity with `ScoringFunction.COSINE`; the gate passed the string `"cosine"`.
