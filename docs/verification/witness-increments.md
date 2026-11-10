# Witness integration verification

| ID | Stimulus | Oracle |
| --- | --- | --- |
| SE-01 | Migrate a v1 history, accept a new event, replay, reuse ID and submit a stale event. | Original v1 receipt, expected conflict and outbox count in PostgreSQL. |
| SE-02 | Deliver cursors 1 and 3, resume intact and exercise a conflicting variant. | Checkpoint 1 before filling the gap and 4 after resume. Conflict stops its feed at 1. |
| SE-03 | Cut the process before and after commit and replay in a fresh process. | Independent SQL, original checksum and persisted receipt. |
| WP-01 | Publish, update, retain obsolete rows, withdraw with a delayed worker and restore. | PostgreSQL/Vespa preserve continuity and exclude the delayed physical projection from search and context. |
| WP-02 | Repeat the history with a controlled index and mutate a real PostgreSQL ACL. | SQL state, expected candidates and context. |
| LX-01 | Hold a stable generation and record lexical candidates and timing. | Vespa rankings, valid empty results and stage timings. |
| RB-01 | Filter exact vectors and fuse two independent ranks. | Calculated fixture and unit tests. |
| DL-01–DL-07 | Revoke before context and during generation. Exercise authority failure, truncation, disconnect, confirmation and admission. | Instrumented backend, returned content or terminal state. |

The early Windows/WSL attempt encountered Vespa's memory check and coordination timeouts. Managed Colab blocked isolated container startup at network or process namespace setup. These attempts provide environment diagnostics. The hosted Linux runner reached the functional test after freeing disk space and verifying feed and search coverage. [Namespace probe](../../infra/vespa/check-container-namespaces.sh) · [Initial runner failure](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42260751640)

Run [42262268040](https://github.com/chrisdeleongarciaa-pixel/mlops-search-retrieval/actions/runs/42262268040) used PostgreSQL 17.11 and Vespa 8.781.11 on two CPUs. The engine had 7.75 GiB of visible RAM and 63% disk use after pulling. WP-01 passed in 2.035 s. LX-01 completed 120 requests with 40 valid empty results and no observed errors. Client p95 was 22.64 ms for Vespa and 64.43 ms for the retrieval path with eligibility reads. [Samples](../../evaluation/evidence/wp01-lx01-actions-42262268040/samples.json) · [Current integration gate](integration-gate.md)

## L4 inference pilot

The isolated pilot served Qwen2.5-7B-Instruct BF16 with vLLM 0.33.0 on an NVIDIA L4. It used Python 3.13.16, PyTorch 2.14.0+cu130, Transformers 5.20.2 and driver 580.82.07. All 11 model files matched their hashes. FlashInfer warmup initially failed because Ninja was absent. The native sampler reached `/health` and passed the chat probe. [Model manifest](../../config/model-sources.json) · [Pilot configuration](../../infra/serving/vllm/README.md)

| Concurrency | Input + output tokens | Completed | Output tokens/s | TTFT p95 | ITL p95 | E2E p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 1,024 + 96 | 16/16 | 16.91 | 276.00 ms | 57.37 ms | 5,692.73 ms |
| 4 | 1,024 + 96 | 16/16 | 57.00 | 1,097.81 ms | 59.65 ms | 6,823.64 ms |
| 8 | 1,024 + 96 | 16/16 | 97.07 | 2,168.63 ms | 60.97 ms | 8,399.80 ms |
| 8 | 3,800 + 256 | 8/8 | 81.63 | 8,215.01 ms | 66.44 ms | 25,066.45 ms |

The raw archive remained in the notebook runtime after a browser download timed out. The table records the observed summary. A later CPU witness covers private delivery with a real model in the same pipeline as retrieval. [Notebook](https://colab.research.google.com/drive/1vkfT1kZGJ2WLb86iAyvSrJwhI93yMjs0) · [RI-01](integration-gate.md#ri-01--private-delivery-with-a-real-model)
