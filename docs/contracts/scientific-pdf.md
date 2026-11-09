# Scientific PDF transformation v1

This profile transforms selectable-text PDFs with GROBID 0.9.1 CRF. It consumes a durable revision and produces artifacts for a candidate generation. Conditional publication consumes that generation in a subsequent integration step.

| ID | Precondition | Postcondition and invariant |
| --- | --- | --- |
| PDF-01 | Identified revision, original SHA-256 and recipe. Limit 20 MiB and 200 pages. | Verify bytes and format before extraction. Distinct errors identify corruption, encryption, no text, limit and temporary failure. |
| PDF-02 | Complete bounded TEI from the pinned parser. | Retain TEI and canonical UTF-8. Abstract and body headings and paragraphs retain section and all original element boxes. This profile excludes figures, tables and bibliography. |
| PDF-03 | Identified tokenizer counts the prompt and special tokens. | Each chunk fits 512 tokens, resolves a half-open canonical-text byte range and carries its SHA-256. Split chunks retain the source paragraph's boxes at declared paragraph granularity. |
| PDF-04 | Same original bytes, revision, TEI, recipe and tokenizer. | Identity and manifest are deterministic. A recipe or generation change changes identity. Incomplete or rejected results lack a publishable manifest. |
| PDF-05 | New output directory and exclusive operator access. | Stage artifacts and complete the manifest and terminal result before renaming. A failed execution retains its identified inputs and rejection. |

Normalize whitespace within each element and join blocks with two line feeds. Byte offsets reference canonical text. Boxes use GROBID PDF coordinates and declare `unavailable` when absent. Check reading order, content and coordinates against an annotated PDF set.

Keep paragraphs independent, with no overlap or cross-section merging. Split long paragraphs at whitespace using the effective token counter. Reject an indivisible unit above budget. The recipe, budget and counter are part of identity. Contract tests use a word-count fixture. The real witness counts with the pinned E5 tokenizer.

The client bounds original bytes, pages, TEI and transport time. The container limits CPU, memory and concurrency. Worker termination after a client timeout requires its own evidence. [Reference snapshot](../../engineering/reference/v1/CONTRATOS_Y_ENTORNO_REPRODUCIBLE_RETRIEVAL.md) · [Verification](../verification/scientific-pdf.md)
