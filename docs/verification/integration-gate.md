# Integration gate and real-model delivery

| Run | Configuration | Coverage |
| --- | --- | --- |
| [42262268040](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42262268040) | `324f937`, manual WP-01/LX-01 | Real PostgreSQL and Vespa publication and lexical retrieval. |
| [42465181926](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42465181926) | `c1fd4fd`, paired stability | E5 and Gemma engine study. [Independent audit](scifact-stability.md) |
| [42541014418](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42541014418) | `56fe913`, push to `main` | Automatic CPU gate including unit and contract suites, 12 PostgreSQL acceptance cases, seven witness cases and LX-01. Dense and hybrid withdrawal use synthetic vectors. |
| [42540081953](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42540081953) | `d9c15ec`, real-model dispatch | RI-01 and MCP-01 completed. [Archived evidence](../../evaluation/evidence/real-inference-42540081953/record.json) |
| [42540643783](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42540643783) | `a593b4f`, pinned Node dispatch | Repeated RI-01 and MCP-01 with Node 25.9.0. |

The CPU job runs on branch pushes and pull requests targeting `main`. It checks selected integration test counts and terminal outcomes. The active rule requires an up-to-date pull request and the named GitHub Actions check. Model and benchmark dispatches remain individually attributable to their commits. [Workflow](../../.github/workflows/wp01.yml) · [Branch rule](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/rules/26820619)

## RI-01 · Private delivery with a real model

The harness accepts a synthetic UTF-8 original, publishes it in Vespa, classifies it as private and supplies an authorized principal. It counts the prompt using the serving template and calls Qwen2.5-1.5B-Instruct Q4_K_M through llama.cpp b12211 in CPU mode. The server uses two threads, one slot and a 1,024-token context. The workflow verifies both binary and model hashes. [Integration test](../../tests/integration/test_witness_pipeline.py)

| Condition | Observed result |
| --- | --- |
| Authorized principal | Answer returned the expected value and cited an eligible fragment. The manifest resolved that citation to original bytes `[0, 54)` and the digest matched. |
| Other principal | Retrieval returned an empty eligible set and generation was refused. |
| Unreachable backend | Private delivery failed closed. |
| Revocation after generation | The final PostgreSQL authority read blocked delivery. |

The first unconstrained run reached generation but produced invalid JSON. The witness now requests JSON Schema with the eligible ID as the only citation value. The application independently verifies citation provenance and the test oracle checks the answer's fact. The two successful runs recorded 4.313 s and 4.371 s for the delivery segment of a single request. [Initial failed run](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42539627794)

```sh
gh workflow run wp01.yml --ref main -f real_inference=true -f dense=false -f scifact=false
```

MCP-01 calls the official filesystem server through a bounded read tool. It returned the 17-day retention value and passed checks for disallowed tools, aliases and paths. The private witness covers a lexical laboratory path with a harness-supplied identity. The authenticated transport, PDF activation and representative load are tracked separately. [MCP example](../../examples/mcp-client/) · [Service objectives](../../engineering/operations/service-objectives.md)
