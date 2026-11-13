# Standard evaluation verification

Contract: [experiment identity and run configuration](../contracts/experiment-identity.md). Code commit `382b632d8b5b7f264c28e9cbd79a8cb2e5ceec5e`, CPython 3.12.10 on Windows amd64 in UTF-8 mode, `config/requirements-bench-win64.lock`, Temurin 21.0.13, Anserini 2.3.0 fatjar. Two CPU cores.

| Requirement | Evidence |
| --- | --- |
| Metric definitions | nDCG@10, RR@10, R@k and Judged@10 equal hand calculations from their definitions, including a graded ranking and a relevant document at rank 11. [Measure tests](../../evaluation/test_measures.py) |
| Rank as written | With tied scores, the written order decides RR@10. Feeding raw scores to the evaluator fails this test. |
| Agreement with the previous evaluator | On the 300 SciFact queries, nDCG@10, R@10 and R@100 equal the earlier direct `pytrec_eval` values with zero difference. |
| Engine output | Queries omitted by the engine are answered with no hits. Unknown, repeated and unordered engine queries are rejected. A failed or unordered stream leaves no output directory. [Run tests](../../tests/unit/test_experiment.py) |
| Pinned inputs | The index digest matches `sha256sum` over literal lines. The downloaded index tarball matched the MD5 published by Anserini before unpacking. |
| Text decoding | The programs run in Python UTF-8 mode and the `ir_datasets` readers refuse to run outside it. |

## Gate: BM25 on MS MARCO passage dev

Measurand: mean RR@10 over the 6,980 queries of `msmarco-passage/dev/small`. Acceptance, fixed before the first run: within 0.001 of 0.187. The run is deterministic.

| Run | Parameters | RR@10 | R@1000 | Published | Source |
| --- | --- | --- | --- | --- | --- |
| [Tuned](../../config/runs/msmarco-dev.anserini-bm25-tuned.toml) | k1 = 0.82, b = 0.68 | 0.18741227770955565 | 0.8573 | MRR@10 0.18741227770955546, recall@1000 0.8573 | Pyserini `docs/experiments-msmarco-passage.md` |
| [Default](../../config/runs/msmarco-dev.anserini-bm25-default.toml) | k1 = 0.9, b = 0.4 | 0.18399 | 0.8526 | MRR 0.1840 | Anserini `docs/installation-fatjar.md` |

Both runs meet the criterion. The tuned run equals the published MRR@10 to fifteen digits. Evidence: [tuned manifest](../../evaluation/evidence/bench-msmarco-dev-anserini-bm25-tuned-1e74b30e140b/manifest.json) · [tuned per-query records](../../evaluation/evidence/bench-msmarco-dev-anserini-bm25-tuned-1e74b30e140b/per-query.jsonl) · [default manifest](../../evaluation/evidence/bench-msmarco-dev-anserini-bm25-default-b9fdbc3ceb7d/manifest.json)

- **Independent recomputation.** A separate script read the 6,974,598 lines of `run.trec` and applied the MS MARCO definition without ir_measures. It returned MRR@10 0.18741227770955565 and recall@1000 0.8573424068767909, equal to the pipeline values in every digit.
- **Documented example.** Query 1048585 ranks passage 7187158 first with RR 1.0, as in the Pyserini guide.
- **Coverage.** All 6,980 queries were answered. Eight returned fewer than 1,000 passages. Judged@10 is 0.041: dev has about one judged passage per query, so nine of ten top results are unjudged and counted as non-relevant.
- **Keys.** Tuned `RunKey` `1e74b30e140bd46475b4b144c205d3027279c16c32ad8ddee5dbdb04e7c145c7`, default `b9fdbc3ceb7d2525f6d90b8260bbeef11c4dc659106c45eefbf51aa41ea487ba`.

## Correction: queries decoded in a legacy code page

The first execution of this gate reported RR@10 0.18736 for the tuned run, 0.00005 below the published figure, and left the difference unexplained.

`ir_datasets` opens its text files without naming an encoding. On Windows the interpreter default is cp1252, so the UTF-8 query file was decoded as other characters with no error. Four of the 6,980 queries hold non-ASCII characters; for example `carnation®` reached the engine as `carnationÂ®`. The pinned query hash described that corrupted text, so the check that should have caught it agreed with it.

The defect surfaced when the same reader failed with `UnicodeDecodeError` on the passage file. After forcing UTF-8 mode and pinning the query hash again, two queries changed their reciprocal rank (1085457 from 0 to 0.125 and 58551 from 0.125 to 0.333). Their sum over 6,980 queries is the whole 0.00005.

The earlier evidence directories were replaced. The byte-identity of two executions was shown on the first run and was not repeated for the corrected one; the ranker and the writer are unchanged.

## Regression suite

`python evaluation/bench.py regress --fast --data-dir var/data` passes: SciFact BM25 nDCG@10 0.6616763 against 0.66168. The SciFact `run.trec` hash is unchanged from the [identity gate](experiment-identity.md), `f7f51fda…`. SciFact is read from archive bytes and was not affected by the decoding defect.

## Not verified

- Pyserini itself was not run. Anserini is the engine it wraps.
- The full `regress` command was not run end to end. Its two MS MARCO checks were executed as individual runs, about ten to thirteen minutes each on this machine when it does not sleep.
- `report` on an unspent test split. TREC DL19 and DL20 are not declared yet.
- Vespa BM25 calibration against this reference, and any Linux runtime.
- The CPU integration gate was not run with these changes.
