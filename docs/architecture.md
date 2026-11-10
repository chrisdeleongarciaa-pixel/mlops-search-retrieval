# Architecture

The Python package separates ingestion, publication, query and delivery. Each use case defines the interfaces it consumes. Adapters bind those interfaces to PostgreSQL, object storage, Vespa, GROBID and a model API. The evaluation programs consume frozen inputs and observable outputs.

| Boundary | Input and output | State owner |
| --- | --- | --- |
| Acceptance | Authorized source event and original bytes → durable receipt | PostgreSQL commits the revision and outbox together. The object store persists the immutable original. |
| Transformation | Durable revision and fixed recipe → canonical text and fragment manifest | The recipe, parser, tokenizer and generation identify the output. |
| Publication | Searchable candidate projection → conditional activation | PostgreSQL owns active revision, generation and eligibility. Vespa stores rebuildable projections. |
| Retrieval | Principal, query and budget → eligible candidates | The authority supplies the eligible snapshot. The index ranks within that snapshot. |
| Context and delivery | Revalidated candidates → bounded context → cited answer | Delivery validates citations against the effective prompt and rechecks access before releasing a private response. |

A publication worker confirms search visibility before activation. The previous publication remains eligible during an ordinary update. A withdrawal changes eligibility at the authority, including when a delayed worker later finishes indexing the withdrawn revision. The PostgreSQL/Vespa witness exercises these races. The PDF transform currently produces a candidate manifest whose activation is a separate integration step. [Acceptance contract](contracts/accept-revision.md) · [Publication decision](../engineering/decisions/0001-publicacion.md) · [PDF contract](contracts/scientific-pdf.md)

`DocumentKey` combines tenant, source and document. `RevisionKey` adds the internal sequence. `ChunkKey` identifies a fragment within a transformation of that revision. The release manifest binds code, corpus, recipe, vector space, index, ranking, model and policy to one configuration. [Release manifest example](../config/release.example.toml)

The private CPU witness runs from ingestion through retrieval, model inference and citation verification. A harness supplies the principal. The target transport and load profile have separate acceptance criteria. [Integration evidence](verification/integration-gate.md) · [Service objectives](../engineering/operations/service-objectives.md)
