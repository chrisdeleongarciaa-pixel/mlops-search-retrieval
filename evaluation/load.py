"""Open-loop load against the retrieval service (SRV-01).

    python evaluation/load.py --url http://127.0.0.1:8090 --corpus manifests/corpus/litsearch.json \
        --queries manifests/queries/litsearch-test.json manifests/queries/litsearch-dev.json \
        --data-dir DIR --out-dir DIR [--routes bm25 dense hybrid] [--rates 10 20 40] \
        [--measured 5000] [--warmup 200] [--repeats 3] [--objective-ms 50 --objective-quantile 0.95]

Arrivals follow a seeded Poisson process. Each request's latency runs from its scheduled send
time to its response, so time spent waiting behind a slow request counts (no coordinated
omission). Warmup requests are discarded. Every measured sample is kept, with the stage times
the service reports. Points are swept by increasing rate; the last rate whose objective quantile
stays under the objective in every repeat is reported, with each percentile's exact 95 %
interval and the spread between repeats.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import platform
import sys
import threading
from time import monotonic, sleep
from urllib.parse import urlsplit

import numpy as np

from search_retrieval.experiment.manifests import read_manifest
from embed import ROOT
from latency import exponential_arrivals, summary
import prepared_collection
from vector_store import _save_atomic, _write_atomic


def load_queries(corpus_manifest: str, query_manifests: list[str], data_dir: Path) -> list[str]:
    corpus, _ = read_manifest(ROOT, corpus_manifest, "corpus")
    texts = []
    for relative in query_manifests:
        query_set, _ = read_manifest(ROOT, relative, "query-set")
        texts += [text for _, text in sorted(prepared_collection.queries(corpus, query_set, data_dir).items())]
    return texts


class Client:
    def __init__(self, url: str, timeout: float):
        parts = urlsplit(url)
        self.host, self.port, self.timeout = parts.hostname, parts.port or 80, timeout
        self.local = threading.local()

    def post(self, body: bytes) -> tuple[int, dict]:
        connection = getattr(self.local, "connection", None)
        if connection is None:
            connection = self.local.connection = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        try:
            connection.request("POST", "/search", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = response.read()
            return response.status, (json.loads(payload) if payload else {})
        except (http.client.HTTPException, OSError):
            connection.close()
            self.local.connection = None
            return 0, {}


def run_point(client: Client, queries: list[str], route: str, rate: float, count: int, seed: int, *,
              k: int, workers: int) -> dict:
    """Send `count` requests at Poisson arrivals and return per-request records in schedule order."""
    offsets = exponential_arrivals(rate, count, seed)
    picks = np.random.default_rng(seed + 1).integers(0, len(queries), count)
    records = [None] * count

    def call(index: int, scheduled: float) -> None:
        started = monotonic()
        body = json.dumps({"query": queries[picks[index]], "route": route, "k": k}).encode("utf-8")
        status, payload = client.post(body)
        finished = monotonic()
        records[index] = {"latency_ms": (finished - scheduled) * 1000, "send_lag_ms": (started - scheduled) * 1000,
                          "status": status, "stages": payload.get("stages_ms", {}), "query": int(picks[index])}

    with ThreadPoolExecutor(workers) as pool:
        origin = monotonic() + 0.5
        for index, offset in enumerate(offsets):
            scheduled = origin + offset
            delay = scheduled - monotonic()
            if delay > 0:
                sleep(delay)
            pool.submit(call, index, scheduled)
    return {"records": records, "duration_s": float(offsets[-1])}


def analyse(records: list[dict], warmup: int) -> dict:
    measured = records[warmup:]
    ok = [record for record in measured if record["status"] == 200]
    latencies = np.array([record["latency_ms"] for record in ok])
    stages = {}
    for name in sorted({stage for record in ok for stage in record["stages"]}):
        values = np.array([record["stages"][name] for record in ok if name in record["stages"]])
        stages[name] = summary(values)
    lags = np.array([record["send_lag_ms"] for record in measured])
    return {"measured": len(measured), "ok": len(ok), "errors": len(measured) - len(ok),
            "error_rate": (len(measured) - len(ok)) / len(measured) if measured else None,
            "latency_ms": summary(latencies) if ok else None, "stages_ms": stages,
            "send_lag_ms": summary(lags) if measured else None}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--queries", nargs="+", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--routes", nargs="+", default=["bm25", "dense", "hybrid"])
    parser.add_argument("--rates", nargs="+", type=float, default=[10, 20, 40])
    parser.add_argument("--measured", type=int, default=5000)
    parser.add_argument("--warmup", type=int, default=200)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--workers", type=int, default=256)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--seed", type=int, default=20261108)
    parser.add_argument("--objective-ms", type=float, default=50.0)
    parser.add_argument("--objective-quantile", type=float, default=0.95)
    parser.add_argument("--cpus", help="pin the generator to these CPUs, e.g. 0-3, away from the service")
    args = parser.parse_args()
    if args.measured < 5000:
        print("Note: SRV-01 reports p99 only with 5,000 or more samples per point", file=sys.stderr)
    if args.cpus and hasattr(os, "sched_setaffinity"):
        first, _, last = args.cpus.partition("-")
        os.sched_setaffinity(0, set(range(int(first), int(last or first) + 1)))
    queries = load_queries(args.corpus, args.queries, args.data_dir)
    lengths = np.array([len(query.split()) for query in queries])
    client = Client(args.url, args.timeout)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    report = {"url": args.url, "routes": {}, "queries": len(queries),
              "query_words": {"p50": float(np.median(lengths)), "p95": float(np.quantile(lengths, 0.95)), "max": int(lengths.max())},
              "measured_per_point": args.measured, "warmup": args.warmup, "repeats": args.repeats, "seed": args.seed,
              "objective": {"quantile": args.objective_quantile, "ms": args.objective_ms},
              "generator": {"workers": args.workers, "cpus": args.cpus, "platform": platform.platform(),
                            "affinity": sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None}}
    key = f"p{round(args.objective_quantile * 100):g}"
    for route in args.routes:
        points, last_passing = [], None
        for rate in sorted(args.rates):
            repeats = []
            for repeat in range(args.repeats):
                seed = args.seed + 1000 * repeat + int(rate * 10)
                outcome = run_point(client, queries, route, rate, args.warmup + args.measured, seed, k=args.k,
                                    workers=args.workers)
                analysed = analyse(outcome["records"], args.warmup)
                latencies = np.array([record["latency_ms"] for record in outcome["records"][args.warmup:]])
                name = f"{route}-rate{rate:g}-repeat{repeat}.npy"
                _save_atomic(args.out_dir / name, np.ascontiguousarray(latencies, dtype="<f8"))
                repeats.append({"repeat": repeat, "seed": seed, "samples_file": name, **analysed})
                print(json.dumps({"route": route, "rate": rate, "repeat": repeat, "errors": analysed["errors"],
                                  key: analysed["latency_ms"][key]["value"] if analysed["latency_ms"] else None}), flush=True)
            values = [item["latency_ms"][key]["value"] for item in repeats if item["latency_ms"]]
            passed = (len(values) == args.repeats and max(values) < args.objective_ms
                      and all(item["errors"] == 0 for item in repeats))
            points.append({"rate": rate, "passed": passed, "repeats": repeats,
                           "between_repeats": {key: {"min": min(values), "max": max(values)} if values else None,
                                               "p99": {"min": min(item["latency_ms"]["p99"]["value"] for item in repeats),
                                                       "max": max(item["latency_ms"]["p99"]["value"] for item in repeats)}
                                               if values else None}})
            if passed:
                last_passing = rate
            else:
                break
        report["routes"][route] = {"points": points, "last_passing_rate": last_passing}
    _write_atomic(args.out_dir / "report.json", (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
    print(json.dumps({"report": str(args.out_dir / "report.json"),
                      "last_passing": {route: item["last_passing_rate"] for route, item in report["routes"].items()}}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
