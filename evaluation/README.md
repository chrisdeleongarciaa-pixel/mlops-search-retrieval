# Evaluation

Declare a run as a hashed configuration under `config/runs/` and execute it with [the runner](bench.py). Its keys, splits and outputs follow the [experiment identity contract](../docs/contracts/experiment-identity.md). The earlier [run manifest template](run.example.toml) remains for the archived evidence. Keep local runs in `evaluation/runs/` and publish verified artifacts with their checksums.

Calibrate the evaluator using [synthetic fixtures](fixtures/README.md). Preserve both branch lists, candidate depth and fused ranking for hybrid experiments. [Evidence index](evidence/README.md)

The [SciFact lexical baseline](evidence/scifact-bm25-v1/manifest.json) uses BEIR qrels and the pinned evaluator. The repository retains its [rankings](evidence/scifact-bm25-v1/run.json) and [per-query metrics](evidence/scifact-bm25-v1/per-query.json). SciFact annotations and abstracts have different terms. [Dataset license](https://github.com/allenai/scifact/blob/master/LICENSE.md)

Run [the paired audit](../docs/verification/scifact-stability.md) to verify engine samples, recompute hybrid metrics and bootstrap matched queries. The [bootstrap CLI](paired_bootstrap.py) also accepts archived score files directly.
