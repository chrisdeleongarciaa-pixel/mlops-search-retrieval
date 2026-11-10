# Paired SciFact stability and quality audit

Run [42465181926](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42465181926) executed E5 and EmbeddingGemma in separate spaces on the same Vespa 8.781.11 host. Docker exposed two CPUs and 8,154,017,792 bytes of RAM. The recorded encoding environments differ. Both produced normalized FP32 vectors stored as `float32` with 768 dimensions. Host matching applies to Vespa measurements and does not identify a causal encoder speed difference.

The registered profile uses 5,183 articles, 809 development queries and the official 300-query BEIR test split. It freezes exploration at zero, requests 100 candidates, and uses one sequential client. The four fresh indices use insertion orders 11, 29, 47 and 83. The same query IDs and eligibility cohorts are replayed across both models and all builds. [Protocol](../../config/scifact-stability-v1.json) · [Encoder decision](../../engineering/decisions/0005-encoder-comparison.md)

## Audit procedure

The audit verifies all 56 files recorded by the eight manifests, checks the freeze and query IDs, and replays algorithm labels from the traces. All eight builds pass recorded exact parity. Full-corpus ANN overlap is recomputed from the saved candidate lists. The audit recomposes RRF for each query from the BM25 and dense branches, matches the saved fusion scores and order, and independently reevaluates all 40 ranking lists with pinned BEIR and qrels. It reproduces the original engine aggregate. The 32 cohort samples contain 9,600 observations and 300 unique queries. [Audit output](../../evaluation/evidence/scifact-paired-42465181926/audit.json)

```sh
gh run download 42465181926 -n scifact-stability -D paired-run
python evaluation/audit_scifact_stability.py paired-run scifact.zip beir-source.zip paired-audit
```

Use the pinned [evaluation environment](../../config/requirements-eval-win64.lock) or its Linux equivalent and an installed project. The audit checks both input archives. The original trace artifact is retained by Actions according to its retention policy. The repository archives the audit, per-build metrics and original per-query score bytes in gzip form. [Evidence index](../../evaluation/evidence/README.md)

## Engine acceptance

Values are the mean and sample standard deviation across four builds. Each row has 300 observations per build. The p95 is the client-observed Vespa call using precomputed query vectors.

| Gemma cohort | Effective algorithm | ANN recall@10 | Overlap@100 | Vespa p95 | RET-01 |
| --- | --- | ---: | ---: | ---: | --- |
| Full | HNSW | 0.99908 ± 0.00032 | 0.97511 ± 0.00111 | 9.44 ± 1.04 ms | Pass in 4/4 |
| ≈50% | HNSW | 0.99958 ± 0.00017 | 0.98688 ± 0.00052 | 9.07 ± 1.18 ms | Pass in 4/4 |
| ≈10% | HNSW | 1.00000 ± 0.00000 | 0.99934 ± 0.00014 | 7.24 ± 0.45 ms | Pass in 4/4 |
| ≈1% | Exact by selectivity | 1.00000 ± 0.00000 | 1.00000 ± 0.00000 | 5.41 ± 0.18 ms | Exact fallback |

Across the 12 Gemma HNSW build/cohort cells, the worst recall@10 was 0.99867 and the largest p95 was 10.57 ms. The thresholds are recall ≥0.95 and p95 <25 ms at this load point. This closes RET-01 for Gemma in the registered single-client engine profile. Service latency, query encoding cost, throughput under load and hybrid latency require separate measurements.

## Retrieval quality

| Full-corpus ranking | NDCG@10 | Recall@10 | Recall@100 |
| --- | ---: | ---: | ---: |
| BM25 | 0.66168 | 0.79094 | 0.88589 |
| E5 exact | 0.69390 | 0.80722 | 0.93933 |
| E5 HNSW | 0.69190 | 0.80639 | 0.94683 |
| E5 exact + BM25 | 0.70202 | 0.82722 | 0.95767 |
| E5 HNSW + BM25 | 0.70161 | 0.82722 | 0.95933 |
| Gemma exact | 0.78871 | 0.91856 | 0.97500 |
| Gemma HNSW | 0.78871 | 0.91856 | 0.97500 |
| Gemma exact + BM25 | 0.74246 | 0.85622 | 0.98000 |
| Gemma HNSW + BM25 | 0.74264 | 0.85622 | 0.98000 |

Gemma HNSW + BM25 scores **0.74259–0.74273** NDCG@10 across the four builds. Exact and HNSW Gemma alone meet RET-02 in every build. Both Gemma fusions miss the 0.75 point target. Fusion adds 0.005 Recall@100 and loses early-rank quality. The quality result comes from re-scoring the observed branch rankings with the frozen RRF configuration.

## Paired uncertainty

The bootstrap uses 10,000 percentile resamples with seed `20261108`. Each draw resamples the same 300 query IDs for both arms. When an arm has four builds, scores are averaged within query before resampling. The interval reflects variation across queries conditional on the observed builds. Build-to-build variation is reported separately above.

| Comparison | Δ NDCG@10 | 95% paired interval | Wins / ties / losses |
| --- | ---: | --- | ---: |
| Gemma exact − E5 exact | +0.09482 | [0.06175, 0.12784] | 91 / 175 / 34 |
| Gemma exact + BM25 − Gemma exact | −0.04626 | [−0.07422, −0.01899] | 37 / 197 / 66 |
| Gemma HNSW + BM25 − Gemma HNSW | −0.04608 | [−0.07405, −0.01873] | 37 / 197 / 66 |
| Gemma HNSW − E5 HNSW | +0.09681 | [0.06317, 0.12994] | 92 / 174 / 34 |

The absolute Gemma HNSW interval is **[0.75269, 0.82372]**. For Gemma HNSW + BM25 it is **[0.70063, 0.78317]**. The intervals describe SciFact queries under this protocol. Transfer to another collection remains an empirical question.

Run an exact-score comparison directly against the archived per-query data. For the four-build estimate, repeat each side's input flag once for each registered seed.

```sh
python evaluation/paired_bootstrap.py \
  --left evaluation/evidence/scifact-paired-42465181926/gemma-seed-11-per-query.json.gz --left-ranker exact \
  --right evaluation/evidence/scifact-paired-42465181926/seed-11-per-query.json.gz --right-ranker exact
```

[Bootstrap script](../../evaluation/paired_bootstrap.py) · [Archived exact run](../../evaluation/evidence/scifact-gemma-exact-cpu/manifest.json) · [Earlier E5 stability](../../evaluation/evidence/scifact-stability-e5-42290872584/aggregate.json)
