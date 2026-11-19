# Vector spaces and canonical vectors

A vector space is a registered description, not a model name. Canonical vectors live in hashed shards outside any engine; an engine index is a projection that can be rebuilt from them. [Verification](../verification/vector-spaces.md) · [Identity contract](experiment-identity.md) · [Store](../../evaluation/vector_store.py) · [CLI](../../evaluation/embed.py)

## Registry

Each file under [`manifests/space/`](../../manifests/space) is a sealed manifest. Its `SpaceKey` hashes the encoder repository and revision, the SHA-256 of every model file, the query and document templates, pooling, normalization, token limit, dtype, dimension, MRL truncation and quantization. `adapter`, `source` and `legacy_id` are annotations and are not hashed. A run configuration names a registered space with `[space.<name>] manifest = "…"` or repeats its fields inline.

| Space | Role | Templates | Limit, dimension, dtype |
| --- | --- | --- | --- |
| `embeddinggemma-300m-fp32` | The only workbench space | `task: search result \| query: {text}` · `title: {title or none} \| text: {text}` | 512, 768, float32 |
| `mteb-embeddinggemma-300m` | Adapter gate only | The configuration under which MTEB published its SciFact figure. | 2048, 768, float32 |
| `multilingual-e5-base-fp32` | Archived evidence only | `query: {text}` · `passage: {text}` | 512, 768, float32 |

Qwen3-Embedding and float16 storage were removed by [decision 0007](../../engineering/decisions/0007-reduced-workbench-scope.md). The store still reads and writes float16 and derived forms.

`{title or none}` renders an empty title as `none`. The transform decides what reaches the template: `title-space-text` joins title and text into `{text}`, `title-text-fields` keeps them apart, `passage` has no title. A change of template, limit, file or dtype is a different space.

The E5 and EmbeddingGemma entries keep the identifiers of the archived evidence in `legacy_id`.

## Vector sets

A vector set holds one space, one role and one collection: documents of a corpus under a transform, or the queries of a query file. Judgments are never read to encode queries.

| Operation | Precondition | Postcondition and invariant |
| --- | --- | --- |
| Encode | Clean work tree. Pinned model snapshot whose files match the space, with nothing unpinned beside them. | Rows are in collection order. Inference is FP32 with autocast off; rows are stored in the dtype of the space. Normalized spaces store unit rows within 1e-5 (float32) or 1e-3 (float16). The shard records how many inputs exceeded the token limit. |
| Seal a shard | Rows, IDs and, for float16, the float32 rows of the parity sample are on disk. | The shard manifest is written last. A shard without it is incomplete and is removed on resume. |
| Resume | Same space, collection, shard size and parity interval. | Complete shards are verified and kept. The collection is replayed: each kept shard must cover the same IDs, or the run stops. The finished set equals an uninterrupted one. |
| Finish | The collection ended and the row count matches the corpus manifest. | `manifest.json` records row count, truncation count and hashes of all row bytes and all IDs in order. Those hashes do not depend on shard boundaries. |
| Verify | Complete set. | Every shard and the set hashes are recomputed. |
| Derive | Verified parent set of a non-quantized space. | Same IDs, order and shard boundaries. The derived space differs only in numeric form and the set records its parent and operation. The encoder is not called. |

| Derived form | Rule |
| --- | --- |
| `mrl:k` | Keep the first `k` dimensions and restore unit length. |
| `binary` | One bit per dimension, set when the value is greater than zero, first dimension in the most significant bit, packed into int8. This is Vespa's `binarize \| pack_bits`. |
| `int8` | `round(x × 127)` clipped to ±127, half to even. |

`encoding.json` beside a set records the code commit, device, package versions, rows per second and peak GPU memory. It describes one execution and is outside the set identity. GPU kernels are not bit-reproducible across hardware, so a vector set is identified by its content hash, not by a promise to regenerate the same bytes.

## Gates

| Gate | Criterion | Command |
| --- | --- | --- |
| Adapter conformance | Maximum absolute error ≤ 1e-5 and cosine ≥ 0.99999 against the reference implementation on fixed inputs, one of them longer than the token limit. | `embed.py conformance` |
| float16 parity | On the fixed sample, every row has cosine ≥ 0.9999 with its float32 original and every query has the same top 10 in both precisions. | `embed.py parity` |
| Published figure | The official MTEB evaluator, driven through the same adapter, returns the published score within 0.5 points. | `mteb_gate.py` |

The parity sample is chosen by ID hash, so it is the same on every run. The report also gives, for queries whose lists differ, the overlap and the largest float32 score gap between documents at the same rank. Those fields do not change the verdict.

[Published figures and their sources](../../config/mteb-published.json) · [GPU procedure](../../infra/gpu/step3-embedding.sh)

## Limits

- MS MARCO has two corpus manifests: the Lucene index read by Anserini and the passage text read for embedding. They describe the same 8,841,823 passages under different digests, so a lexical and a dense run do not yet share a corpus identity.
- SPLADE and ColBERT are not registered. Sparse and multi-vector rows need their own stored form.
- The adapter gate compares against MTEB's configuration, which differs from the workbench spaces in token limit and, for EmbeddingGemma, in templates. It validates the adapter code path, not the workbench templates.
- The pinned model revisions differ from the revisions MTEB evaluated.
- Text read through `ir_datasets` requires Python UTF-8 mode. The programs enforce it. [Text mode](../../src/search_retrieval/experiment/textmode.py)
