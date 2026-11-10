# Bibliographic and PDF evidence

`evidence-index.json` binds results and local code/configuration hashes to the base `c1fd4fd`. The published recalculation input record replaces absolute paths with filenames and retains original manifest and input hashes. Source corpus and weights remain external.

`pdf-manifest.json`, `pdf-inputs.json` and `pdf-result.json` identify the successful PostgreSQL/GROBID run. The three `failed-*.json` files retain earlier rejections. The access log distinguishes client timeout from server completion. `pdf-container-effective.txt` records memory, memory plus swap, NanoCPUs and image ID in order.

`pdf-independent-token-check.py` preserves the verifier as executed from the original workspace. Its hash appears in the count result. The reusable range verifier is `evaluation/verify_transformation.py`. Reproduce the full witness using the integration test and runbook.

The LitSearch manifest records the frozen selection and inherited judgments. The SciFact recalculation and BM25 regeneration are separate records. [Verification](../../../docs/verification/scientific-pdf.md) · [Runbook](../../../engineering/operations/scientific-pdf.md)
