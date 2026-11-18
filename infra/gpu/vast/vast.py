"""Choose, rent, watch and destroy a vast.ai VM for the workbench, from the operator's machine.

    python infra/gpu/vast/vast.py offers --phase A [--top 15]
    python infra/gpu/vast/vast.py watch-offers --phase A --max-cost 2.5 [--timeout-min 60]
    python infra/gpu/vast/vast.py create OFFER_ID --phase A [--label workbench-a]
    python infra/gpu/vast/vast.py wait-ssh INSTANCE_ID [--timeout-min 25]
    python infra/gpu/vast/vast.py monitor INSTANCE_ID --out DIR --max-hours 7 [--reserve 0.3]
    python infra/gpu/vast/vast.py destroy INSTANCE_ID
    python infra/gpu/vast/vast.py credit

The API key is read from the file named by VAST_API_KEY_FILE and sent only to console.vast.ai.
Standard library only. Cost estimates come from PROFILES below; they are planning figures, not
measurements, and the run's own timings should replace them once a phase has been run.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API = "https://console.vast.ai/api/v0"
VM_IMAGE = "docker.io/vastai/kvm:ubuntu_terminal"
# Compute capabilities with a pinned TEI 1.9.4 image in run-all.sh.
TEI_CAPS = (800, 860, 890, 900)
REFERENCE_TFLOPS = 35.0   # an RTX 3090 as vast.ai reports total_flops; the profiles' GPU hours refer to it
REFERENCE_CORES = 16

# Planning figures per phase (hours on the reference GPU and cores; GB downloaded by the host).
PROFILES = {
    "A": {"fixed_h": 2.3 + 0.4, "gpu_h": 0.4, "cpu_h": 1.0, "download_gb": 12.0,
          "min": {"cpu_ram": 30_000, "disk_space": 80, "cpu_cores_effective": 8}, "disk": 80},
    "B": {"fixed_h": 0.5, "gpu_h": 2.5, "cpu_h": 7.0, "download_gb": 20.0,
          "min": {"cpu_ram": 64_000, "disk_space": 200, "cpu_cores_effective": 16}, "disk": 200},
}


def api_key() -> str:
    path = os.environ.get("VAST_API_KEY_FILE")
    if not path:
        sys.exit("Set VAST_API_KEY_FILE to a file holding the vast.ai API key")
    return Path(path).read_text(encoding="utf-8").strip()


def call(method: str, path: str, body=None, query: dict | None = None, retries: int = 3):
    url = API + path
    if query:
        url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
    data = None if body is None else json.dumps(body).encode()
    request = Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + api_key(), "Content-Type": "application/json", "User-Agent": "workbench-vast"})
    for attempt in range(retries):
        try:
            with urlopen(request, timeout=60) as response:
                text = response.read().decode()
                return json.loads(text) if text else {}
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"{method} {path}: HTTP {error.code} {error.read()[:300]!r}") from None
        except (URLError, TimeoutError) as error:
            if attempt + 1 < retries:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"{method} {path}: {error}") from None
    raise AssertionError


def log(message: str, path: Path | None = None) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {message}"
    print(line, flush=True)
    if path:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")


# --- Offers -----------------------------------------------------------------------------------

def search(phase: str) -> list[dict]:
    profile = PROFILES[phase]
    query = {"verified": {"eq": True}, "external": {"eq": False}, "rentable": {"eq": True},
             "rented": {"eq": False}, "vms_enabled": {"eq": True}, "num_gpus": {"eq": 1},
             "gpu_ram": {"gte": 23_000}, "cuda_max_good": {"gte": 13.0},
             "compute_cap": {"in": list(TEI_CAPS)}, "reliability": {"gte": 0.97},
             "inet_down": {"gte": 300}, "order": [["dph_total", "asc"]], "type": "on-demand",
             "limit": 200, "allocated_storage": profile["disk"]}
    for field, minimum in profile["min"].items():
        query[field] = {"gte": minimum}
    result = call("POST", "/bundles/", query)
    if not result:
        raise RuntimeError("The offer search returned nothing")
    offers = result["offers"]
    return [offer for offer in offers if offer.get("compute_cap") in TEI_CAPS]


def estimate(offer: dict, phase: str) -> dict:
    """Expected hours and cost of a phase on an offer, from the planning profile."""
    profile = PROFILES[phase]
    tflops = max(float(offer.get("total_flops") or 0), 1.0)
    cores = max(float(offer.get("cpu_cores_effective") or 1), 1.0)
    down_mbps = max(float(offer.get("inet_down") or 1), 1.0)
    hours = (profile["fixed_h"] + profile["gpu_h"] * REFERENCE_TFLOPS / tflops
             + profile["cpu_h"] * REFERENCE_CORES / min(cores, 64)
             + profile["download_gb"] * 8_000 / down_mbps / 3600)
    reliability = max(float(offer.get("reliability2") or offer.get("reliability") or 0.5), 0.5)
    cost = offer["dph_total"] * hours / reliability + profile["download_gb"] * float(offer.get("inet_down_cost") or 0)
    return {"hours": round(hours, 2), "cost": round(cost, 3), "price": round(offer["dph_total"], 4)}


def ranked(phase: str) -> list[tuple[dict, dict]]:
    pairs = [(offer, estimate(offer, phase)) for offer in search(phase)]
    return sorted(pairs, key=lambda pair: (pair[1]["cost"], pair[1]["hours"]))


def describe(offer: dict, cost: dict) -> str:
    return (f"{offer['id']:>9} {offer['gpu_name'][:12]:<12} cc {offer['compute_cap']} "
            f"cpu {offer.get('cpu_cores_effective', 0):>4.0f} ram {offer.get('cpu_ram', 0) / 1000:>4.0f}G "
            f"disk {offer.get('disk_space', 0):>5.0f}G down {offer.get('inet_down', 0):>5.0f} "
            f"rel {offer.get('reliability2') or offer.get('reliability', 0):.3f} ${cost['price']:.3f}/h "
            f"est {cost['hours']:.1f} h ${cost['cost']:.2f}  {offer.get('geolocation', '')}")


def command_offers(args) -> int:
    for offer, cost in ranked(args.phase)[:args.top]:
        print(describe(offer, cost))
    return 0


def command_watch_offers(args) -> int:
    """Poll the market until the best offer's expected cost is within --max-cost."""
    deadline = time.time() + args.timeout_min * 60
    while True:
        pairs = ranked(args.phase)
        if pairs and pairs[0][1]["cost"] <= args.max_cost:
            offer, cost = pairs[0]
            log("chosen: " + describe(offer, cost))
            print(json.dumps({"offer": offer["id"], **cost, "gpu": offer["gpu_name"],
                              "machine": offer.get("machine_id")}))
            return 0
        best = describe(*pairs[0]) if pairs else "no offer matches"
        if time.time() > deadline:
            log(f"no offer within ${args.max_cost} after {args.timeout_min} min; best: {best}")
            return 1
        log(f"waiting: best {best}")
        time.sleep(args.poll_s)


# --- Instances --------------------------------------------------------------------------------

def show(instance_id: int) -> dict | None:
    result = call("GET", f"/instances/{instance_id}/", query={"owner": "me"})
    if not result:
        return None
    return result.get("instances") if isinstance(result.get("instances"), dict) else result


def command_create(args) -> int:
    body = {"client_id": "me", "image": VM_IMAGE, "disk": PROFILES[args.phase]["disk"], "label": args.label,
            "runtype": "ssh_direc ssh_proxy", "env": {}, "onstart": None, "python_utf8": False,
            "lang_utf8": False, "force": False, "cancel_unavail": True}
    result = call("PUT", f"/asks/{args.offer}/", body, retries=1)
    print(json.dumps(result))
    return 0 if result and result.get("success") else 1


def ssh_targets(instance: dict) -> list[tuple[str, int]]:
    targets = []
    ports = (instance.get("ports") or {}).get("22/tcp") or []
    if instance.get("public_ipaddr") and ports:
        targets.append((instance["public_ipaddr"].strip(), int(ports[0]["HostPort"])))
    if instance.get("ssh_host") and instance.get("ssh_port"):
        targets.append((instance["ssh_host"], int(instance["ssh_port"])))
    return targets


SSH_OPTIONS = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=20",
               "-o", "ServerAliveInterval=30", "-o",
               "UserKnownHostsFile=" + str(Path.home() / ".ssh" / "known_hosts.vast")]


def ssh(target: tuple[str, int], command: str, timeout: int = 120, stdout: Any = subprocess.PIPE):
    host, port = target
    return subprocess.run(["ssh", *SSH_OPTIONS, "-p", str(port), f"root@{host}", command],
                          stdout=stdout, stderr=subprocess.PIPE, timeout=timeout)


def booted(instance: dict) -> bool:
    """VM instances stay at actual_status "created" while running; cur_state reports the machine."""
    return "running" in (instance.get("actual_status"), instance.get("cur_state"))


def reachable(instance: dict) -> tuple[str, int] | None:
    for target in ssh_targets(instance):
        try:
            if ssh(target, "true", timeout=40).returncode == 0:
                return target
        except subprocess.TimeoutExpired:
            pass
    return None


def command_wait_ssh(args) -> int:
    deadline = time.time() + args.timeout_min * 60
    while time.time() < deadline:
        instance = show(args.instance)
        if instance is None:
            log("instance not found")
            return 1
        status = f"{instance.get('actual_status')}/{instance.get('cur_state')}"
        target = reachable(instance) if booted(instance) else None
        if target:
            print(json.dumps({"host": target[0], "port": target[1], "status": status}))
            return 0
        log(f"status {status} ({instance.get('status_msg', '')[:80]}); ssh not yet reachable")
        time.sleep(20)
    log(f"ssh not reachable after {args.timeout_min} min")
    return 1


def destroy(instance_id: int, journal: Path | None = None) -> bool:
    for attempt in range(1, 6):
        try:
            call("DELETE", f"/instances/{instance_id}/", {}, retries=1)
        except RuntimeError as error:
            log(f"destroy attempt {attempt} failed: {error}", journal)
        time.sleep(10)
        if show(instance_id) is None:
            log(f"instance {instance_id} destroyed", journal)
            return True
    log(f"instance {instance_id} still listed after five destroy requests: destroy it in the console", journal)
    return False


def command_destroy(args) -> int:
    return 0 if destroy(args.instance) else 1


def credit() -> float:
    user = call("GET", "/users/current/")
    if not user:
        raise RuntimeError("No account data")
    return float(user["credit"])


def command_credit(_args) -> int:
    print(f"{credit():.4f}")
    return 0


# --- Monitor ----------------------------------------------------------------------------------

REMOTE = "/root/mlops-search-retrieval/var"


def pull(target, remote_command: str, path: Path, timeout: int = 600) -> bool:
    part = path.with_name(path.name + ".part")
    with part.open("wb") as stream:
        try:
            result = ssh(target, remote_command, timeout=timeout, stdout=stream)
        except subprocess.TimeoutExpired:
            return False
    if result.returncode != 0 or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        return False
    part.replace(path)
    return True


def command_monitor(args) -> int:
    """Copy reports every poll; destroy the instance when the guard on it finishes or anything fails."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    journal = out / "monitor.log"
    started = time.time()
    last_contact = time.time()
    log(f"monitor of {args.instance}: max {args.max_hours} h, reserve ${args.reserve}, poll {args.poll_s} s", journal)

    def end(reason: str) -> int:
        log(f"ending instance: {reason}", journal)
        return 0 if destroy(args.instance, journal) else 1

    while True:
        try:
            instance = show(args.instance)
        except RuntimeError as error:
            log(f"API error: {error}", journal)
            instance = {"actual_status": "unknown"}
        if instance is None:
            log("instance no longer exists", journal)
            return 0
        age_h = (time.time() - float(instance.get("start_date") or started)) / 3600 if instance.get("start_date") else (time.time() - started) / 3600
        try:
            balance = credit()
        except RuntimeError:
            balance = None
        target = reachable(instance) if booted(instance) else None
        if target:
            last_contact = time.time()
            pull(target, f"cd {REMOTE} && tar -czf - --ignore-failed-read reports stages pipeline.log watchdog.log "
                         f"pipeline.exit final.json 2>/dev/null", out / "snapshot.tar.gz", timeout=300)
            tail = ssh(target, f"tail -n 1 {REMOTE}/pipeline.log; cat {REMOTE}/final.json 2>/dev/null", timeout=60)
            lines = tail.stdout.decode(errors="replace").strip().splitlines()
            log(f"running {age_h:.2f} h, ${instance.get('dph_total', 0):.3f}/h, credit {balance}, "
                f"last line: {lines[0][:120] if lines else ''}", journal)
            if any(line.startswith("{\"reason\"") for line in lines):
                final = next(line for line in lines if line.startswith("{\"reason\""))
                log(f"guard finished: {final}", journal)
                name = ssh(target, f"ls -1t {REMOTE}/results-*.tar.gz | head -n 1", timeout=60).stdout.decode().strip()
                if name and pull(target, f"cat {name}", out / Path(name).name, timeout=1800):
                    log(f"copied {Path(name).name}", journal)
                else:
                    log("could not copy the results archive; the snapshot is the last copy", journal)
                return end(json.loads(final)["reason"])
        else:
            log(f"no ssh contact (status {instance.get('actual_status')}) for "
                f"{(time.time() - last_contact) / 60:.0f} min", journal)
        if balance is not None and balance < args.reserve:
            return end(f"credit {balance:.2f} below the reserve {args.reserve}")
        if time.time() - last_contact > args.unreachable_min * 60:
            return end(f"unreachable for {args.unreachable_min} min")
        if age_h > args.max_hours + 0.5:
            return end(f"older than {args.max_hours} h plus 30 min: the guard on the instance did not end it")
        time.sleep(args.poll_s)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    offers = commands.add_parser("offers")
    offers.add_argument("--phase", choices=PROFILES, default="A")
    offers.add_argument("--top", type=int, default=15)
    watch = commands.add_parser("watch-offers")
    watch.add_argument("--phase", choices=PROFILES, default="A")
    watch.add_argument("--max-cost", type=float, required=True)
    watch.add_argument("--timeout-min", type=float, default=60)
    watch.add_argument("--poll-s", type=float, default=120)
    create = commands.add_parser("create")
    create.add_argument("offer", type=int)
    create.add_argument("--phase", choices=PROFILES, default="A")
    create.add_argument("--label", default="retrieval-workbench")
    wait = commands.add_parser("wait-ssh")
    wait.add_argument("instance", type=int)
    wait.add_argument("--timeout-min", type=float, default=25)
    monitor = commands.add_parser("monitor")
    monitor.add_argument("instance", type=int)
    monitor.add_argument("--out", required=True)
    monitor.add_argument("--max-hours", type=float, required=True)
    monitor.add_argument("--reserve", type=float, default=0.3)
    monitor.add_argument("--poll-s", type=float, default=120)
    monitor.add_argument("--unreachable-min", type=float, default=30)
    end = commands.add_parser("destroy")
    end.add_argument("instance", type=int)
    commands.add_parser("credit")
    args = parser.parse_args()
    handler = {"offers": command_offers, "watch-offers": command_watch_offers, "create": command_create,
               "wait-ssh": command_wait_ssh, "monitor": command_monitor, "destroy": command_destroy,
               "credit": command_credit}[args.command]
    return handler(args)


if __name__ == "__main__":
    sys.exit(main())
