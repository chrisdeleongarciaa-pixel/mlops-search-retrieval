# Vector space verification

Contract: [vector spaces and canonical vectors](../contracts/vector-spaces.md). Verified on CPython 3.12.10, Windows amd64, numpy 2.5.4, without torch and without model weights. The runs that load the model are under [Executed on a GPU](#executed-on-a-gpu).

| Requirement | Evidence |
| --- | --- |
| Row and ID hashes | Hashes of float32 and float16 row bytes and of ID lines equal `sha256sum` over literal bytes. [Store tests](../../evaluation/test_vector_store.py) |
| Set identity independent of sharding | The same rows written with shards of 2 and of 5 give equal row and ID hashes. |
| Resume | A write stopped after two shards, with orphan rows and a temporary file left behind, resumes to a set equal to the uninterrupted one. A reordered or shorter collection, another identity and another shard size are rejected. [Embedding tests](../../evaluation/test_embed.py) |
| Integrity | A changed row, a changed ID, a missing shard manifest and an extra shard each fail verification. Non-unit, non-finite, mistyped, duplicated and oversized shards are rejected before anything is written. |
| Derived forms | Packed signs reproduce the five examples of the Vespa binarization guide. int8 rounding and MRL truncation equal hand calculations. A derived set records its parent and reuses its IDs and shard boundaries. |
| float16 parity | A random float16 set passes. A store with two rows swapped fails. Two rows that float16 stores identically fail with overlap 1.0 and a reported score gap of 1e-4. |
| Registry | Recomputing the earlier E5 and EmbeddingGemma identifiers from their adapters gives the `space_id` values of the [archived evidence](../../evaluation/evidence/scifact-gemma-exact-cpu/manifest.json). All registered spaces are distinct. |
| Snapshot and download | A snapshot with a wrong file, a missing file or an unpinned file is rejected. A download that does not match its hash is discarded. |
| Rendering and order | Templates render as the model cards write them. Rows return to collection order after batching by length. Truncation is counted against the token limit. |

Thirteen seeded faults were each detected: bit order and threshold of sign packing, int8 rounding, the shard hash comparison, removal of orphan files, MRL renormalization, the parity verdict, tie breaking, the unit-norm check, the resume ID check, row order after length batching, the truncation count and the unpinned-file check. The row-order fault first survived: test inputs grew with their index, so batching by length was the identity. The inputs were made non-monotonic and the fault was then detected.

## End-to-end run on SciFact with the synthetic space

The synthetic encoder derives a 64-dimension unit vector from a hash of the rendered text. It exercises the pipeline on real documents and says nothing about retrieval quality. Code commit `7e0d355`.

| Step | Result |
| --- | --- |
| Encode 5,183 documents, float16, shards of 500 | Vector set `66f92d470b269dd9399f7e541554a9cba12edf919b585c6b7dc738fa2d1e7499`, 11 shards. |
| Simulated crash | The set manifest and the manifests of the last three shards were deleted, leaving their rows. The next run kept 4,000 rows, encoded 1,183 and produced the same vector-set digest. A resume with another shard size was refused. |
| Encode queries | 1,109 queries of the SciFact query file, vector set `2c06b8c32eb00c78890c624b10cb1001c2dc0bdcc69e61081912dde15dd369cf`. |
| Derive `binary`, `mrl:32`, `int8` | Three sets, each verified and each recording the parent digest. |
| float16 parity, 1,062 sampled documents | Minimum cosine 0.99999996. Identical top 10 for 1,097 of 1,109 queries: **the gate fails**. |

### Parity finding

The twelve queries that differ keep 0.9996 of the exact top 10 on average and never less than 0.9. The largest float32 score gap between documents at the same rank is 4.7e-5. float16 did not lose neighbours; near-tied neighbours changed places.

Random 64-dimension vectors have closely packed scores, so this run overstates how often it happens. It still shows that "identical top 10 for every query" can fail from ties alone, with cosine far above its limit. The criterion is implemented as specified. Whether it should tolerate differences below the float16 score resolution is open until the real embeddings report their overlap and gap.

## Published MTEB figures

Read on 2026-11-12 from the MTEB results repository at commit `f8fa68f43a0d3868334c764cec9d21ce39c7ecbc`. [Checks](../../config/mteb-published.json)

| Model | SciFact test nDCG@10 | Model revision evaluated | MTEB version | This repository, BEIR evaluator |
| --- | --- | --- | --- | --- |
| intfloat/multilingual-e5-base | 0.6965 | `d13f1b27…` | 1.20.0 | 0.6939 at revision `d1287505…` |
| google/embeddinggemma-300m | 0.78675 | `64614b0b…` | 2.27.1 | 0.78871 at revision `57c266a7…` |
| Qwen/Qwen3-Embedding-0.6B | 0.69718 | `b22da495…` | 1.38.9 | Not measured |

The two archived exact results are within 0.5 points of the published figures, with another evaluator, other revisions and, for EmbeddingGemma, another document template. That is consistent with the adapters and is not the gate, which requires the official evaluator.

On SciFact the published Qwen3-Embedding-0.6B figure is 9 points below EmbeddingGemma. Its higher multilingual average does not transfer to this collection.

## Executed on a GPU

[`infra/gpu/step3-embedding.sh`](../../infra/gpu/step3-embedding.sh) through `run-all.sh`, CPython 3.12.10, torch 2.14.0, transformers 5.20.2, sentence-transformers 6.2.0, mteb 2.27.12, installed from the Linux GPU lock. [Evidence](../../evaluation/evidence/gpu-litsearch-rtx3090-2026-11-18/reports) · [Second GPU](../../evaluation/evidence/gpu-msmarco-encoding-rtx4090-2026-11-19/reports)

| Check | RTX 3090, 2026-11-18 | RTX 4090, 2026-11-19 | Verdict |
| --- | --- | --- | --- |
| Adapter conformance, `embeddinggemma-300m-fp32`, with an input of 8,010 tokens | max absolute error 3.1e-7, min cosine 0.99999999999 | 2.4e-7, 0.99999999999 | SATISFIED |
| MTEB SciFact nDCG@10 through the adapter | 0.78675 (rerun at `1d6d69e`) | 0.78675 in the pipeline | SATISFIED: published 0.78675, tolerance 0.5 points |
| Vector sets | SciFact and LitSearch corpora and queries, verified | Those again and MS MARCO passage (8,841,823 rows) with its 6,980 dev queries, verified | SATISFIED |

The MTEB figure was produced at revision `57c266a` with mteb 2.27.12; the published one at `64614b0` with mteb 2.27.1. Equality to five decimals is plausible if the weights are the same across those revisions, which was not checked.

## Not executed

| Check | Status |
| --- | --- |
| float16 parity on real embeddings | Not executed: float16 was removed from the pipeline by decision 0007. |
| E5 and Qwen3 spaces on a GPU | Not executed: removed by decision 0007. |

## Corrections made during verification

- **MTEB similarity (found on the GPU).** mteb 2.27.12 compares the model's similarity by identity with `ScoringFunction.COSINE`, and `ModelMeta.create_empty` keeps the string `"cosine"` as given; the gate failed with "Unsupported similarity function". It now passes the enum.
