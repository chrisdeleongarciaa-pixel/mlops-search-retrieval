# Engine projection, serving, ACL and fusion verification

Contract: [engine projection, serving, ACL and fusion](../contracts/serving.md). Verified on CPython 3.12.10, Windows amd64, with HTTP fakes of Vespa and TEI. No step here has run against Vespa, TEI or PostgreSQL.

| Requirement | Evidence |
| --- | --- |
| Feed and read back | Feeding retries overload responses (429) and stops on other errors; vectors read back equal the float32 rows exactly. [Client tests](../../evaluation/test_vespa_passages.py) |
| Filters | Collection, cohort and ACL token filters select exactly the expected documents; revoking a token removes the document from filtered results. |
| BM25 grammar | The query sends `{grammar: "any", defaultIndex: "text"}userInput(@q)`. |
| Exact parity rule | Identical lists agree; a swap at a tie within 1e-5 agrees; a missing neighbour, a score gap or a short list disagrees. |
| Engine runs | `vespa-bm25`, `vespa-dense` and `vespa-hybrid` produce the lists of the fusion functions applied to the engine's branches; inconsistent configurations are rejected. |
| Fusion | Min-max scaling and convex combination equal hand calculations; alpha 0 and 1 reproduce each branch's order. The choice rule and the promotion gate behave as specified. [Fusion tests](../../evaluation/test_fusion_sweep.py) |
| Service | Each route reports its stages; a private document needs a grant; a revocation at the authority excludes it at delivery before the engine update; an unreachable authority fails the request. The HTTP front end answers 200, 400 and 503 as specified. [Service tests](../../evaluation/test_serving.py) |
| TEI client | The rendered prompt is sent; non-unit, mis-shaped and failed responses are rejected. |
| Open-loop load | With one worker and a 0.3 s stall, later requests' latency includes their wait from the scheduled time, and the generator's send lag exposes it. |
| Percentile intervals | The ranks equal the workbench table for 300, 1,200, 5,000 and 10,000 samples; scipy's binomial gives each at least 95 % coverage. |
| ACL-01 measurement | A revocation is excluded at delivery without leaks; an authority that does not revoke, with an engine update 0.2 s late, leaks and is counted. |
| Declarations | Generated run configurations resolve; the Linux runtime manifest pins CPython 3.12.10 and the GPU lock. |

## Not executed

All of it needs the VM described in [the GPU procedure](../../infra/gpu/README.md).

| Check | Status |
| --- | --- |
| The `passage` schema deploying on Vespa 8.781.11, including `filter-first-threshold` | Not executed. Written against the current Vespa reference, not that version's. |
| Projection of SciFact, LitSearch and MS MARCO; step 5 parity | Not executed. |
| Engine BM25, dense and fused runs; fusion choice; test report | Not executed. |
| TEI 1.9.4 serving EmbeddingGemma in float32 from the local snapshot; TEI parity | Not executed. That TEI reads both dense projection layers is inferred from its release notes. |
| PostgreSQL authority, ACL-01 | Not executed. The integration test is skipped without a database. |
| SRV-01 | Not executed. |
| `run-all.sh` and the compose stack | Not executed. Syntax checked, and every flag the script passes exists in the CLI it calls (`--help` of each, 2026-11-17). |
| Watchdog against vast.ai | Not executed. With `curl` stubbed and a fake pipeline it packs and sends one stop request when the pipeline ends, at the budget deadline, and with the grace period cut to the deadline; it refuses a non-numeric price instead of running without a deadline. |

## Corrections made during verification

- **Budget guard failing open.** The deadline was computed with `python3`; where it is missing the deadline was empty and every comparison failed, so the watchdog never stopped on budget. It is now computed with `awk` and an invalid deadline stops the watchdog at start, which the launch check shows.
- **Watched process.** The launch recorded `$!` of `setsid`, which forks when started from an interactive shell (util-linux behaviour, not observed here), so the watchdog would have seen the pipeline end at once. `run-all.sh` now records its own process ID.
