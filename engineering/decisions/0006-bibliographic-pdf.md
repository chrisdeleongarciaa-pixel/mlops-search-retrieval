# 0006 · Bibliographic domain and scientific PDFs

Adopt English ML/NLP retrieval over title and abstract, with relevance by article. Freeze LitSearch by revision and SHA-256. Select 300 queries by provenance and specificity and exclude connected positives from development. Source judgments retain their original meaning.

Qwen2.5-7B-Instruct is the primary rewrite candidate and Gemma-3-1B-IT is the comparator to pin. The external clinical extraction result motivates an experiment. This domain requires its own fidelity and retrieval evidence. Use GROBID 0.9.1 CRF to preserve TEI, canonical text, sections and page coordinates. The local profile uses one worker and deferred loading. Compare another parser when annotated extraction identifies a measurable gap.

Reconsider the configuration after evidence of coverage loss, memory pressure or target incompatibility. Preserve revision identity and withdrawal during PDF generation activation. [Bibliographic contract](../../docs/contracts/bibliographic-profile.md) · [PDF contract](../../docs/contracts/scientific-pdf.md) · [Operational limits](../operations/scientific-pdf.md)
