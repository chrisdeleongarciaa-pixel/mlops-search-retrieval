# Standard evaluation verification

Contract: [experiment identity and run configuration](../contracts/experiment-identity.md). Code commit `a6dde5a37d4480d620485fc0f5f3f276ee257746`, CPython 3.12.10 on Windows amd64, `config/requirements-bench-win64.lock`, Temurin 21.0.13, Anserini 2.3.0 fatjar. Two CPU cores.

| Requirement | Evidence |
| --- | --- |
| Metric definitions | nDCG@10, RR@10, R@k and Judged@10 equal hand calculations from their definitions, including a graded ranking and a relevant document at rank 11. [Measure tests](../../evaluation/test_measures.py) |
| Rank as written | With tied scores, the written order decides RR@10. Feeding raw scores to the evaluator fails this test. |
| Agreement with the previous evaluator | On the 300 SciFact queries, nDCG@10, R@10 and R@100 equal the earlier direct `pytrec_eval` values with zero difference. |
| Engine output | Queries omitted by the engine are answered with no hits. Unknown, repeated and unordered engine queries are rejected. A failed or unordered stream leaves no output directory. [Run tests](../../tests/unit/test_experiment.py) |
| Pinned inputs | The index digest matches `sha256sum` over literal lines. The downloaded index tarball matched the MD5 published by Anserini before unpacking. |

## Gate: BM25 on MS MARCO passage dev

Measurand: mean RR@10 over the 6,980 queries of `msmarco-passage/dev/small`. Acceptance, fixed before the run: within 0.001 of 0.187. The run is deterministic.

| Run | Parameters | RR@10 | R@1000 | Published | Source |
| --- | --- | --- | --- | --- | --- |
| [Tuned](../../config/runs/msmarco-dev.anserini-bm25-tuned.toml) | k1 = 0.82, b = 0.68 | 0.18736 | 0.8573 | MRR@10 0.18741, recall@1000 0.8573 | Pyserini `docs/experiments-msmarco-passage.md` |
| [Default](../../config/runs/msmarco-dev.anserini-bm25-default.toml) | k1 = 0.9, b = 0.4 | 0.18396 | 0.8526 | MRR 0.1840 | Anserini `docs/installation-fatjar.md` |

Both runs meet the criterion. Evidence: [tuned manifest](../../evaluation/evidence/bench-msmarco-dev-anserini-bm25-tuned-a5f7250907aa/manifest.json) · [tuned per-query records](../../evaluation/evidence/bench-msmarco-dev-anserini-bm25-tuned-a5f7250907aa/per-query.jsonl) · [default manifest](../../evaluation/evidence/bench-msmarco-dev-anserini-bm25-default-1d53ee81d1f9/manifest.json)

- **Independent recomputation.** A separate script read `run.trec` line by line and applied the MS MARCO definition without ir_measures. It returned MRR@10 0.18736452221767405 and recall@1000 0.8573424068767909, equal to the pipeline values in every digit.
- **Documented example.** Query 1048585 ranks passage 7187158 first with RR 1.0, as in the Pyserini guide.
- **Reproducibility.** Two executions of the tuned run produced byte-identical `run.trec` (6,974,598 lines, 433 MB), `per-query.jsonl` and `manifest.json`. `RunKey` `a5f7250907aa4ebfdfe2a19c327859fc8e6e331f64ff811cb3b7bcda6a813582`.
- **Coverage.** All 6,980 queries were answered. Eight returned fewer than 1,000 passages. Judged@10 is 0.041: dev has about one judged passage per query, so nine of ten top results are unjudged and counted as non-relevant.

The tuned figure is 0.00005 below the published one. The Pyserini guide builds its own index and this run uses the prebuilt index with Anserini 2.3.0. That difference was not investigated.

## Regression suite

`python evaluation/bench.py regress --fast --data-dir var/data` passes: SciFact BM25 nDCG@10 0.6616763 against 0.66168. The SciFact `run.trec` hash is unchanged from the [identity gate](experiment-identity.md), `f7f51fda…`. Its `per-query.jsonl` and `RunKey` changed because the evaluator and the runtime manifest changed. [SciFact manifest](../../evaluation/evidence/bench-scifact-bm25-step2/manifest.json)

## Not verified

- Pyserini itself was not run. Anserini is the engine it wraps.
- The full `regress` command was not run end to end. Its two MS MARCO checks were executed as individual runs, which take from minutes to over an hour on this machine.
- `report` on an unspent test split. TREC DL19 and DL20 are not declared yet.
- Vespa BM25 calibration against this reference, and any Linux runtime.
- The CPU integration gate was not run with these changes.
