# Dense and hybrid retrieval

| Operation | Precondition | Postcondition and invariant |
| --- | --- | --- |
| Encode | Verified model snapshot, retrieval role and fixed FP32 profile. | Return one finite normalized 768-dimensional vector per input in original order. E5 excludes padding from pooling. The 512-token limit includes prompt and special tokens. |
| Project | Durable revision and generation bound to one vector space. | Verify the fragment set through search before activation. The space participates in projection identity. PostgreSQL retains provenance and controls activation. |
| Retrieve | Authorized tenant, query, budget and compatible generation. | Rank within eligibility and revalidate before returning. Retry once within budget after a change. An empty compatible snapshot is valid. Partial coverage, missing vectors and incompatible generations fail explicitly. |
| Fuse | Complete lexical and dense lists from the same snapshot. | Apply equal-weight RRF with depth 100 and constant 60. Break ties by document ID. Recheck authority before context reuse. |

The mutable witness permits up to 400 eligible fragments. PostgreSQL supplies fragment identities from manifests and Vespa verifies presence and vector space. Report coverage verification cost separately. The immutable SciFact generation verifies the complete corpus after loading.

A worker can physically write a withdrawn projection. PostgreSQL's activation fence keeps it ineligible and revalidation excludes it from prior results. The SciFact oracle computes FP64 cosine on stored `float32` vectors with ID tie-breaking. Vespa uses `prenormalized-angular` and `1 - distance(field, embedding)`. The score tolerance `1e-5` handles boundary ties and strict ID overlap remains available. [Service objectives](../../engineering/operations/service-objectives.md) · [Verification](../verification/dense-retrieval.md)
