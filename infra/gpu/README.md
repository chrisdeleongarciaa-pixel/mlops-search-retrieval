# Running the workbench on a rented GPU

[`run-all.sh`](run-all.sh) runs the reduced workbench end to end ([decision 0007](../../engineering/decisions/0007-reduced-workbench-scope.md)). Phase A deploys and measures the LitSearch system; phase B adds MS MARCO (`B_ENGINE=0` keeps the vectors, oracle and HNSW sweeps and leaves out the Vespa projection). Every stage is recorded in `var/stages` once finished, so running the script again resumes from the first unfinished stage.

## Host

| Requirement | Why |
| --- | --- |
| A vast.ai **VM** instance (`vms_enabled`, image `docker.io/vastai/kvm:ubuntu_terminal`) | Standard instances are containers and cannot run Docker; PostgreSQL, Vespa and TEI run as containers. |
| NVIDIA GPU with 24 GB or more | torch 2.14 and FAISS 1.16 need a CUDA 13 driver. The VM image ships guest driver 535; [`vast/bootstrap.sh`](vast/bootstrap.sh) installs 580 and asks for a reboot. TEI images are pinned for compute capability 8.0, 8.6, 8.9 and 9.0. |
| NVIDIA Container Toolkit in Docker | Installed by `bootstrap.sh`; TEI runs on the GPU. |
| 16 or more CPU cores, 48 GB of RAM, 120 GB of disk | Measured: an offer listed with 64 GB gave the VM 49 GB. An HNSW build over the 8.8 M MS MARCO rows holds about 31 GB. Phase A used under 10 GB of disk. |

## Rent, launch and guard

[`vast/vast.py`](vast/vast.py) runs on the operator's machine (standard library only; the API key file is named by `VAST_API_KEY_FILE`):

| Command | What it does |
| --- | --- |
| `offers --phase A` (`A`, `B`, `B-ann`) | Lists VM offers that meet the phase's minimum resources, ranked by expected cost: price × estimated hours ÷ reliability, plus download cost. The hours come from `PROFILES`, scaled by the offer's TFLOPS, cores and bandwidth; `B-ann` is calibrated on the phase A timings. |
| `watch-offers --phase A --max-cost C` | Polls the market until the best offer's expected cost is within the limit. |
| `create OFFER --phase A --authorized-key ~/.ssh/id_ed25519.pub` | Rents the offer as a VM. A VM takes SSH keys only when it is created; the key is also written by its start script. |
| `wait-ssh INSTANCE` | Waits until the VM answers on SSH. |
| `monitor INSTANCE --out DIR --max-hours H` | Every two minutes copies the reports and logs and applies the guard's decision (below). Low credit (`--reserve`), a VM unreachable for `--unreachable-min` minutes and an instance 30 minutes past `--max-hours` are stopped. Time during which the vast.ai API itself is unreachable does not count. |
| `destroy INSTANCE` | Destroys the instance and checks that it is gone. |

[`vast/launch.sh`](vast/launch.sh) copies HEAD to the VM as a git bundle, runs `bootstrap.sh` (rebooting once for the driver), writes the secrets to root-only files outside the clone, starts `run-all.sh` and [`watchdog.sh`](watchdog.sh) detached and checks that both run:

```bash
B_ENGINE=0 VAST_API_KEY_FILE=... HF_TOKEN_FILE=... bash infra/gpu/vast/launch.sh INSTANCE HOST PORT PRICE_PER_HOUR BUDGET_USD MAX_HOURS B
```

The guard uses the instance's own `CONTAINER_API_KEY`; the account key is placed on the VM only when that key is missing.

## What the guard does

| Trigger | Action |
| --- | --- |
| `run-all.sh` exits with 0 | Pack the results, wait 15 minutes for the copy, **destroy**. The monitor destroys as soon as it has the archive. |
| `run-all.sh` fails or vanishes | Pack, then **hold** the instance 60 minutes (`HOLD_MIN`) with its vectors, indexes and stages. Running `run-all.sh` again resumes from the failed stage and the guard watches the new process. Without a resume the instance is **stopped**. |
| No file under `var/` changes for 45 minutes at low load | Same as a failure. |
| `MAX_HOURS`, or 90 % of `BUDGET_USD / PRICE_PER_HOUR`, minus the 15-minute grace | Pack, wait for the copy, **stop**. |

A stopped instance keeps its disk and can be started again when its GPU is free; destroy it once its results are no longer needed. [`test_watchdog.sh`](test_watchdog.sh) exercises every trigger with a stubbed `curl`.

To resume after a fix, on the VM: `cd /root/mlops-search-retrieval && git pull /path/to/fix.bundle main` (changes outside the declaration paths need a new run declaration), then start `run-all.sh` again as `launch.sh` does.

## Measured run times

| Host | Work | Time |
| --- | --- | --- |
| RTX 3090 VM, 30 vCPU | Boot, driver install and reboot, dependencies | about 25 min |
| RTX 3090 VM | Phase A, all stages | 1.96 h, of which SRV-01 1.6 h |
| RTX 3090 VM | Vespa feed with HNSW | about 650 documents per second |
| RTX 3090 VM | FAISS HNSW, M 16, efConstruction 200, 64,183 rows | 8.5–9.2 s per build |
| RTX 4090 VM, 23 vCPU | EmbeddingGemma float32 on MS MARCO passages | about 1,100 passages per second; 8,841,823 in 2.3 h |

## Results

`var/reports` holds one file per gate: `conformance-*.json`, `mteb-*.json`, `oracle-*.jsonl`, `sweep-*.jsonl` and `sweeps/`, `engine-parity-*.jsonl`, `fusion-dev.json`, `fusion-test-gate.json`, the test report manifests, `tei-parity-*.json`, `acl-01.json`, `srv-01/report.json` and, with the engine in phase B, `bm25-calibration.json`.

The run declarations generated on the host are committed in its clone (`config/runs/generated`); the guard's archive carries them as patches (`patches/`) to apply with `git am`. Recorded runs are in [evaluation/evidence](../../evaluation/evidence/README.md).
