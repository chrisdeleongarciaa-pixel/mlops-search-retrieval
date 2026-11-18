# Running the workbench on a rented GPU

[`run-all.sh`](run-all.sh) runs the reduced workbench end to end ([decision 0007](../../engineering/decisions/0007-reduced-workbench-scope.md)). Phase A deploys and measures the LitSearch system; phase B adds MS MARCO. Every stage is recorded in `var/stages` once finished, so an interrupted run resumes.

## Host

| Requirement | Why |
| --- | --- |
| A vast.ai **VM** instance (Extra Filters: `vms_enabled=true`, Ubuntu 22.04 VM template) | Standard instances are containers and cannot run Docker; PostgreSQL, Vespa and TEI run as containers. |
| NVIDIA GPU with 24 GB or more and a CUDA 13 driver (580 or later) | torch 2.14 and FAISS 1.16 are built for CUDA 13. TEI images are pinned for compute capability 8.0, 8.6, 8.9 and 9.0. |
| NVIDIA Container Toolkit in Docker | TEI runs on the GPU. The script stops if Docker has no NVIDIA runtime. |
| 16 or more CPU cores, 64 GB of RAM, 150 GB of disk | Phase B holds 27 GB of MS MARCO vectors in Vespa and builds HNSW over the same rows in FAISS. Phase A alone needs about 32 GB of RAM and 40 GB of disk. |

## Launch

Accept the EmbeddingGemma terms on Hugging Face first. From a clean checkout of the delivered archive on the VM:

```bash
export HF_TOKEN=...            # read access to google/embeddinggemma-300m
export VAST_API_KEY=...        # used only by the watchdog to stop the instance
export INSTANCE_ID=...         # this instance, as shown in the vast.ai console
mkdir -p var && rm -f var/pipeline.pid
setsid nohup env PHASES=A bash infra/gpu/run-all.sh </dev/null >var/pipeline.log 2>&1 &
setsid nohup env BUDGET_USD=5 PRICE_PER_HOUR=0.45 bash infra/gpu/watchdog.sh </dev/null >var/watchdog.log 2>&1 &
sleep 5; cat var/watchdog.log; ps -p "$(cat var/pipeline.pid)"; pgrep -f watchdog.sh
tail -f var/pipeline.log
```

`run-all.sh` writes its own process ID to `var/pipeline.pid`; `$!` is not usable because `setsid` forks when launched from an interactive shell. Set `PRICE_PER_HOUR` to the instance's real price. Before closing the SSH session, check that `watchdog.log` names a process ID, that `ps` shows it and that `pgrep` finds the watchdog.

The watchdog stops the instance at 90 % of the budget. When the pipeline ends first, it packs `var/reports`, the logs and the JSON records of oracles, sweeps and projections into `var/results-*.tar.gz` and stops the instance `GRACE_MIN` minutes later (15 by default, never past the budget). Copy the archive during that window:

```bash
scp -P <port> root@<host>:mlops-search-retrieval/var/results-*.tar.gz .
```

Stopping keeps the disk, which vast.ai still bills at its storage rate, and restarting a stopped instance needs its GPU to be free again. Destroy the instance once the results are copied.

## Rent, launch and guard with `vast/`

[`vast/vast.py`](vast/vast.py) runs on the operator's machine (standard library only; the API key file is named by `VAST_API_KEY_FILE`):

| Command | What it does |
| --- | --- |
| `offers --phase A` | Lists VM offers with one GPU of 24 GB or more, a CUDA 13 driver and a compute capability that has a pinned TEI image, ranked by expected cost: price × estimated hours ÷ reliability, plus download cost. The hours come from planning figures per phase (`PROFILES`), scaled by the offer's TFLOPS, cores and bandwidth. |
| `watch-offers --phase A --max-cost 2.5` | Polls the market until the best offer's expected cost is within the limit. |
| `create OFFER --phase A` | Rents the offer as a VM (`docker.io/vastai/kvm:ubuntu_terminal`, SSH). |
| `wait-ssh INSTANCE` | Waits until the VM answers on SSH. |
| `monitor INSTANCE --out DIR --max-hours H` | Every two minutes copies the reports and logs, then destroys the instance when the guard on it has finished (after copying its results archive), when credit drops below `--reserve`, when the VM is unreachable for 30 minutes, or 30 minutes past `--max-hours`. |
| `destroy INSTANCE` | Destroys the instance and checks that it is gone. |

[`vast/launch.sh`](vast/launch.sh) copies HEAD to the VM as a git bundle, runs [`vast/bootstrap.sh`](vast/bootstrap.sh) (Docker, compose, NVIDIA Container Toolkit, a GPU check from a container), writes the secrets to root-only files outside the clone, starts `run-all.sh` and `watchdog.sh` detached and checks both. On the VM the watchdog destroys the instance on success, failure, 45 minutes without any file changing under `var/` at low load, or `MAX_HOURS`, after packing the results and waiting 15 minutes for the monitor to copy them. The account key is placed on the VM only when vast.ai provides no `CONTAINER_API_KEY` there; rotate it after the run.

## Budget

Run times were not measured. These are estimates from the size of the work and will be off by a factor of two or more.

| Phase | Work | Estimate |
| --- | --- | --- |
| A | Install, model, SciFact and LitSearch embedding and oracles, three HNSW builds of 64,183 vectors, Vespa feed, parity, nine engine runs, TEI parity, 1,000 revocations | 1–2 h |
| A | SRV-01: 3 routes × 3 rates × 3 repeats × 5,200 requests (`RATES`, `MEASURED`, `REPEATS`) | 2.3 h of arrivals at 10, 20 and 40 queries per second |
| B | 8.8 M passages embedded in float32 | 2–4 h |
| B | Oracle in blocks, three HNSW builds of 8.8 M vectors on CPU | 3–9 h |
| B | Vespa feed of 8.8 M documents, parity, two dev runs | 1–3 h |

With five dollars, run `PHASES=A` first. Phase A ends in a deployed LitSearch system with every gate measured except the MS MARCO ones.

## Results

`var/reports` holds one file per gate: `conformance-*.json`, `mteb-*.json`, `oracle-*.jsonl`, `sweep-*.jsonl` and sweep summaries, `engine-parity-*.jsonl`, `fusion-dev.json`, `fusion-test-gate.json`, the test report manifests, `tei-parity-*.json`, `acl-01.json`, `srv-01/report.json` and, in phase B, `bm25-calibration.json` with the MS MARCO run manifests.

The run declarations generated on the host are committed in its clone (`config/runs/generated`). Bring them back with the reports, for example with `git format-patch` from the delivered commit.
