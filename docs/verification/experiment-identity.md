# Experiment identity verification

Contract: [experiment identity and run configuration](../contracts/experiment-identity.md). Code commit `def272015553a5de58d3de1947920f631a023b33`, CPython 3.12.10 on Windows amd64, `config/requirements-eval-win64.lock`.

| Requirement | Evidence |
| --- | --- |
| One serialization, externally checked | Digests of a sealed body, a `TransformKey`, a `QuerySetKey` and a TREC run equal `sha256sum` over literal bytes quoted in the tests. [Key tests](../../tests/unit/test_sealed_identity.py) |
| A member change is another key | Each `SpaceKey` and `RunKey` member is varied alone and yields a distinct digest. Table and key order leave every key unchanged. |
| Complete resolution | A changed manifest value fails until resealed and then yields another `TransformKey`, `GenerationKey` and `RunKey`. Unknown, missing, mistyped and unfilled values are rejected. [Run tests](../../tests/unit/test_experiment.py) |
| Code and runtime binding | Real throwaway Git repositories cover a directory inside another repository, a dirty tree, an unknown commit, a commit on another branch and code committed after `code_commit`. Interpreter, lock digest and installed pin mismatches are rejected. |
| Closed test split | `run` rejects `test`. `report` rejects a missing or non-development frozen configuration, one that differs outside the query set, and one last changed in `HEAD`. It accepts the same pair after a later commit. |
| Reproducible outputs | Two writes of one run agree byte for byte on `run.trec`, `per-query.jsonl` and `manifest.json` while their timings differ. A modified run file fails verification. |

Nine seeded faults, one per check above, each made at least one test fail: the freeze-before-`HEAD` rule, the post-commit path filter, the repository top-level check, the frozen-digest comparison, key sorting, the query-set exclusion from the frozen digest, the manifest digest comparison, MRL truncation in `SpaceKey` and placeholder rejection.

## Gate: a SciFact run rebuilt from its configuration

`config/runs/scifact-regression.bm25-reference.toml` was executed twice into new directories.

```sh
python evaluation/bench.py run config/runs/scifact-regression.bm25-reference.toml --data-dir var/data --output evaluation/runs/gate-1
```

| File | SHA-256, identical in both executions |
| --- | --- |
| `run.trec` | `f7f51fdad496ccd4ae623d270fad6d44f7871aa1b3f2931781421bdfc87f3d35` |
| `per-query.jsonl` | `ba54b95c15559c28e82ddb699acfb36e54105711c4bed2d1a1c8ef09c55b4935` |
| `manifest.json` | `291d54ce1817b164773972a1e7ff0c0350b943348fee8e9b458e2cbda4294d93` |

`RunKey` `07d90e3f23a49b85702d0ab454a194e1beaff7b860f1521c4f4214f9e90d9991`. [Manifest](../../evaluation/evidence/bench-scifact-bm25-07d90e3f23a4/manifest.json) · [Per-query records](../../evaluation/evidence/bench-scifact-bm25-07d90e3f23a4/per-query.jsonl)

The same configuration at an earlier code commit produced the same `run.trec` and `per-query.jsonl` and a different `RunKey` and manifest.

## Agreement with the archived baseline

The archived [SciFact BM25 run](../../evaluation/evidence/scifact-bm25-v1/manifest.json) was produced before this work. Against it, the new run has the same 300 queries, identical document order and identical scores for every query, and identical per-query `ndcg_cut_10` and `recall_100`. Aggregates round to NDCG@10 0.66168, Recall@10 0.79094 and Recall@100 0.88589. The archive member hashes of the corpus, queries and test judgments equal those recorded by the archived manifest.

Both runs call the same `bm25` function, so this shows that the new loader, runner and output format preserve the baseline. It is not an independent check of the ranker.

## Not verified

- No `report` execution on a real test split: no collection with an unspent test split is declared yet. The lock is exercised by the unit tests.
- Byte identity on another machine or on Linux. The runtime manifest covers Windows amd64 only.
- The CPU integration gate was not run with these changes.
- The archived baseline records a different digest for `requirements-eval-win64.lock` than the current file. Later evidence records the current digest.
