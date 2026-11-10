"""Report the resources available to a managed Colab runtime as JSON."""

import json
import os
import platform
import shutil
import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def read_optional(path):
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def package_version(name):
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def nvidia_smi(*args):
    try:
        return subprocess.check_output(
            ["nvidia-smi", *args], text=True, stderr=subprocess.STDOUT, timeout=10
        ).strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return {"error": type(exc).__name__}


def main():
    if not Path("/content").is_dir():
        raise SystemExit("Run this probe in a Colab Linux runtime with /content.")

    memory = {}
    for line in (read_optional("/proc/meminfo") or "").splitlines():
        key, _, value = line.partition(":")
        if key in {"MemTotal", "MemAvailable"}:
            memory[key] = int(value.split()[0]) * 1024

    disk = shutil.disk_usage("/content")
    report = {
        "architecture": platform.machine(),
        "python": platform.python_version(),
        "cpu_affinity_count": len(os.sched_getaffinity(0)),
        "cpu_cgroup_max": read_optional("/sys/fs/cgroup/cpu.max"),
        "ram_bytes": memory,
        "memory_cgroup_max_bytes": read_optional("/sys/fs/cgroup/memory.max"),
        "content_disk_bytes": {"total": disk.total, "free": disk.free},
        "docker_cli_present": shutil.which("docker") is not None,
        "packages": {name: package_version(name) for name in ("torch", "vllm")},
        "nvidia_smi_version": nvidia_smi("--version"),
        "gpu_csv": nvidia_smi(
            "--query-gpu=name,memory.total,memory.free,driver_version",
            "--format=csv",
        ),
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
