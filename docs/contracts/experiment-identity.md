# Experiment identity and run configuration

A run is one TOML file. Every figure traces to hashed keys, and a change in any key is a different experiment. [Verification](../verification/experiment-identity.md) · [Runner](../../evaluation/bench.py)

## Keys

Each sealed key is the SHA-256 of `{"body": …, "kind": …, "schema_version": 1}` in [canonical JSON](../../src/search_retrieval/contracts/canonical.py): UTF-8, sorted keys, no insignificant whitespace, finite numbers, integers distinct from floats. [Key types](../../src/search_retrieval/contracts/identity.py)

| Key | Members | Declared in |
| --- | --- | --- |
| `DocumentKey`, `RevisionKey` | Tenant, source, document, sequence. Unchanged. | PostgreSQL |
| `TransformKey` | Parser and version, configuration, chunking policy, reference tokenizer. | Transform manifest. The manifest digest is the key. |
| `ContentChunkKey` | `RevisionKey`, `TransformKey`, ordinal, UTF-8 byte range, content SHA-256. | Derived. `ChunkKey` stays the identity of fragments already published. |
| `SpaceKey` | Encoder repository and revision, file hashes, query and document templates, pooling, normalization, token limit, dtype, dimension, MRL truncation, quantization. | `[space.<name>]`. `legacy_id` records an earlier space identifier and is not hashed. |
| `GenerationKey` | Corpus manifest digest, `TransformKey`, sorted set of `SpaceKey`, engine, schema revision, `hnsw`, `filter` and `lexical` parameters, insertion seed. | `[corpus]`, `[transform]`, `[space]`, `[index]` |
| `QuerySetKey` | Name, split, query and judgment file hashes, judgment source. | Query-set manifest |
| `RunKey` | `GenerationKey`, ranking profile, target hits, fusion, `QuerySetKey`, code commit, runtime manifest digest. | Whole configuration |

`[run].id` and `[load]` are labels and measurement settings. They are recorded in the run manifest and stay outside `RunKey`.

## Manifests

A manifest is `{"kind", "schema_version", "body", "sha256"}`. Loading recomputes the digest of the body. A changed value fails until the manifest is resealed, and a resealed manifest yields different keys. The digest covers values, so reformatting a manifest keeps its identity. References are repository-relative and cannot leave the repository. Unknown and missing fields are errors.

```sh
python evaluation/bench.py seal manifests/corpus/beir-scifact.json
```

## Operations

| Operation | Precondition | Postcondition and invariant |
| --- | --- | --- |
| Resolve | Complete configuration. Every referenced manifest matches its digest. No `<placeholder>` values. | All keys are built or the run is rejected. Table and key order do not affect any key. |
| Bind to code | The project is the top level of its own Git work tree. The tree has no uncommitted or untracked files. `code_commit` is a full commit ID and an ancestor of `HEAD`. | Between `code_commit` and `HEAD`, only `config/runs/`, `manifests/`, `docs/`, `engineering/` and `evaluation/evidence/` differ. |
| Bind to runtime | Runtime manifest with interpreter version, platform tag and lock file digests. | The interpreter and platform match, each lock file matches its digest and every pinned package is installed at its pinned version. |
| `run` | Split `dev` or `regression`. | Test judgments are never opened. |
| `report` | Split `test` and `--frozen-dev` naming a `dev` configuration. | The two configurations agree on every `RunKey` member except the query set. The development configuration was last changed in a commit before `HEAD`. |
| Write | New output directory. | `run.trec`, `per-query.jsonl` and `manifest.json` are byte-identical across executions of the same `RunKey` in the same runtime. `manifest.json` records all keys, the configuration, the environment and the hashes of the other two files. |

## Splits

| Split | Meaning | Reader |
| --- | --- | --- |
| `dev` | Parameter selection. | `run` |
| `test` | Reported once, after the configuration is frozen. | `report` |
| `regression` | A test split already used for selection or reporting. It checks the pipeline and supports no improvement claim. | `run` |

SciFact `test` is declared `regression`: its judgments selected and reported the earlier results. [Query-set manifest](../../manifests/queries/beir-scifact-test.json)

## Outputs

| File | Content | Reproducible |
| --- | --- | --- |
| `run.trec` | `qid Q0 doc rank score tag`, queries in ID order, ties already broken by document ID. Scores use the shortest round-trip decimal form. | Yes |
| `per-query.jsonl` | Generation, effective ANN strategy, terminal state, returned depth and metrics, one canonical JSON object per query. | Yes |
| `manifest.json` | Keys, configuration, environment, aggregate metrics and the hashes of the two files above. | Yes |
| `timings.json` | Stage timings. Named by the manifest and excluded from its hashes. | No |

Stage timings live outside `per-query.jsonl` so that the reproducible files can be compared by hash.

## Limits

- Git ordering shows that a configuration was committed before the report. It cannot show that test judgments were never inspected by other means.
- `report` refuses an existing output directory. A second report into another directory is not prevented.
- Byte identity is claimed within one runtime manifest. Another platform is another `RunKey`.
- `bm25-reference` is the in-process ranker of the SciFact baseline. Anserini parity and Vespa calibration have separate gates.
