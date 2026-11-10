# Bibliographic retrieval and query rewriting v1

The profile covers English ML/NLP literature. Represent each article as title + U+0020 + abstract and judge relevance at article level. LitSearch provides queries for this domain. SciFact has an independent test split and judgments.

| ID | Input obligation | Guarantee or acceptance criterion |
| --- | --- | --- |
| BIB-01 | Seven pinned files with expected byte counts and SHA-256. | Verify 64,183 unique articles and 597 queries with resolvable positives. Materialize 300 queries by provenance and specificity quotas and seeded SHA-256 order. The test checks permutation stability. |
| BIB-02 | Freeze selection before ranking. | Development excludes components connected by shared positives or identical normalized query text. Retain IDs and hashes. Topic separation and pretraining contamination require separate evidence. |
| BIB-03 | Complete ranking by query ID with unique candidates. | Compute Recall@5 and Recall@20 against inherited positives. Unjudged items remain unjudged. Manual cases exercise recall and reject incomplete coverage. |
| RW-01 | Valid original and complete JSON with `finish_reason=stop`. | Reject extra or duplicate fields, invalid actions, empty text and truncation. `keep` returns the original unchanged. |
| RW-02 | Required literals are explicit and present in the original. | Check literal preservation. Independent semantic judgments assess negation, relations, entities and constraints. |
| RW-03 | Identical frozen queries, schema, prompt, retriever, snapshot and qrels across arms. | Compare original, Qwen and Gemma by query and specificity. Retain rankings, outputs, Recall@5/20 and lost or altered constraints. Record invalid output as a failure. A fallback to original constitutes a separate policy. |
| RW-04 | Pin model revisions, hashes, environment and generation parameters. | Record time to validated JSON, tokens, termination and precision. Establish fidelity, quality and latency before enabling rewriting. |

Qwen2.5-7B-Instruct is the primary rewrite candidate. Gemma-3-1B-IT is the generative comparator to pin. EmbeddingGemma-300M is a retrieval encoder. The frozen LitSearch selection has 300 test queries, 258 development queries and 39 related queries held out. Its judgments come from the source collection. [Decision](../../engineering/decisions/0006-bibliographic-pdf.md) · [Protocol](../../config/rewrite-experiment.json) · [Verification](../verification/scientific-pdf.md)
