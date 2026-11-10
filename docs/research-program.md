# Learned retrieval research program

The platform provides durable revisions, controlled eligibility and repeatable retrieval experiments. Research experiments bind the corpus, query IDs, qrels, code, encoder and index to one manifest. Production promotion and research claims have separate criteria.

## Reference and uncertainty

Use the audited SciFact paired run as the reference. Preserve the four insertion orders, 300 official test queries and frozen retrieval parameters. Bootstrap matched query IDs and report graph variation separately. The original BEIR test split stays fixed after development. GPU encoder and transport performance require dedicated measurements. [Verified reference](verification/scifact-stability.md)

## Transfer and reranking

Run the frozen lexical, exact, ANN and fusion branches on an additional BEIR collection and BRIGHT before tuning against their test sets. Retain complete candidate lists and negative results. Use candidate coverage at depth 100 to bound the opportunity for a reranker, then evaluate a cross-encoder on identical candidates. Report first-stage, reranking and total latency with declared budgets. For LitSearch, keep ranking and query-rewrite runs paired on the frozen domain queries and retain independent semantic judgments. [Bibliographic contract](contracts/bibliographic-profile.md)

## Representation experiments

Start with a specific failure observed in exact rankings. Freeze an intervention, a control and a held-out transfer test. Candidate mechanisms include hard-negative selection, contrastive objective, teacher distillation and multi-vector scoring. A useful experiment holds candidate construction and evaluation constant where possible and reports what changed in quality, coverage, compute and memory. Model selection between two pretrained checkpoints is a baseline for these studies.

Retain each external benchmark's own task definition. SciFact BEIR ranks articles. Original SciFact claim verification and evidence sentence selection require their own metrics. MS MARCO and SCIDOCS retain their protocol-specific units and judgments. [Archived protocol register](../engineering/reference/v1/CONTRATOS_Y_ENTORNO_REPRODUCIBLE_RETRIEVAL.md)
