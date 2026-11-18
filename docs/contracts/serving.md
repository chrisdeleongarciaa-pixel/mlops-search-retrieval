# Engine projection, serving, ACL and fusion

Steps 5 to 8 of the reduced workbench ([decision 0007](../../engineering/decisions/0007-reduced-workbench-scope.md)). [Verification](../verification/serving.md) · [GPU procedure](../../infra/gpu/README.md)

## Projection (step 5)

| Element | Definition |
| --- | --- |
| Document type | [`passage`](../../infra/vespa/app/schemas/passage.sd): collection label, document ID, BM25 text, cohort bucket, ACL tokens, float32 embedding. The witness `fragment` type is unchanged. |
| HNSW and strategies | M 16, 200 neighbors at insert. Post-filtering off (1.0), filter-first below 30 % eligible, exact search below 2 %. `maxHits` 1,000. |
| Collection label | Digest of the vector set, the corpus, the BM25 text transform and the whole application package. |
| Feed | In collection order; a vector row whose ID differs from the document stops the feed. Resumes from the last finished window. |
| Acceptance of a projection | The collection count equals the corpus size, and a fixed sample read back holds exactly the canonical float32 rows, text and cohort bucket. |
| Gate | `approximate:false` returns the oracle's top 100 per cohort on a fixed query sample, up to ties within 1e-5. ANN recall per query is reported per cohort and exploration with the strategy traced by the engine. |
| BM25 | `userInput` with `grammar: "any"`, an exhaustive OR. `bm25` uses Vespa's defaults (k1 1.2, b 0.75); `bm25-msmarco` uses k1 0.82, b 0.68 to compare with the Anserini reference. |

## Service (steps 6 and 7)

`python -m search_retrieval.serving.http` answers `POST /search` with `{query, route, k, principal?}` for the routes `bm25`, `dense` and `hybrid`, and returns the hits with per-stage times. [Service](../../src/search_retrieval/serving/service.py)

| Stage | Behaviour |
| --- | --- |
| Encode | TEI with `--dtype float32`. The client renders the space's query template; TEI applies no prompt of its own. Rows must be finite, of the space's dimension and unit length within 1e-4. |
| Engine | Filter on ACL tokens before the top k: `public`, and the principal for documents granted to it. Branch depth 100 for hybrid. |
| Fusion | RRF (k = 60) or convex with min-max scaling and the alpha chosen on development. |
| Revalidation | The PostgreSQL authority removes any returned private document the principal may no longer read. Its failure fails the request (503). |

| Gate | Criterion |
| --- | --- |
| TEI parity | Every LitSearch test and development query: maximum absolute difference ≤ 1e-5 and cosine ≥ 0.99999 against the canonical batch vector. |
| ACL-01 | At least 1,000 revocations on a collection that is not served; no response after the commit holds the revoked document; every delivery exclusion under 1 s. Engine exclusion is reported beside it. |
| SRV-01 | Poisson arrivals, latency from the scheduled send time, 5,000 measured samples per point after 200 warmup, 3 repeats, the generator pinned to its own cores. p50, p95, p99 with exact 95 % intervals, stage percentiles, error rate, the generator's send lag and the last rate whose p95 stays under 50 ms in every repeat. |

## Fusion (step 8)

| Element | Definition |
| --- | --- |
| Candidates | RRF k = 60 and convex alpha in 0, 0.05, …, 1, from BM25 and dense development runs at depth 1,000, computed offline. |
| Choice | Highest mean nDCG@10 on LitSearch development; a tie goes to RRF, then to the smaller alpha. |
| Report | The chosen fusion and both branches are declared on test with the same code commit as their development runs and reported once with `bench.py report`. |
| Promotion | The paired 95 % interval of the fusion minus the better branch must exclude zero, on development and again on test. |

## Limits

- The service serves search results; no generation context is served, so ACL-01 covers search and delivery.
- The engine and TEI share the host with the load generator. Only the generator and the service are pinned to separate cores.
- LitSearch judgments are inherited positives; Judged@10 reports how many results were judged.
- A revocation committed between revalidation and the response's emission is not excluded from that response. The window is the time after the revalidation stage.
