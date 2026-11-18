#!/usr/bin/env bash
# Prepare a vast.ai Ubuntu 22.04 VM (docker.io/vastai/kvm:ubuntu_terminal) for run-all.sh, as root.
#
#   bash infra/gpu/vast/bootstrap.sh
#
# Installs what is missing: git, curl, Docker with the compose plugin and the NVIDIA Container
# Toolkit (from NVIDIA's apt repository), then checks that a container sees the GPU. Idempotent.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# vast.ai writes multi-line, unquoted values (PUBLIC_KEY, SSH_PUBLIC_KEY) to /etc/environment, so
# any script that sources it as shell fails; dpkg's install-info trigger is one (exit 127, observed
# 2026-11-18). Keep NAME=value lines only and quote values that contain spaces; keep a backup.
if [ ! -f /etc/environment.vast ]; then
    cp /etc/environment /etc/environment.vast
    awk '/^[A-Za-z_][A-Za-z0-9_]*=/ {
            name = substr($0, 1, index($0, "=") - 1); value = substr($0, index($0, "=") + 1)
            if (value ~ / / && value !~ /^".*"$/) value = "\"" value "\""
            print name "=" value }' /etc/environment.vast > /etc/environment.new
    mv /etc/environment.new /etc/environment
    dpkg --configure -a >/dev/null 2>&1 || true
fi

apt_install() { apt-get install -y -q --no-install-recommends "$@" >/dev/null; }
apt-get update -q >/dev/null
apt_install git curl ca-certificates gnupg python3 python3-pip util-linux

# The VM's guest driver, not the host's, decides the CUDA version: the image ships 535 (CUDA
# 12.2) while torch 2.14 and FAISS 1.16 need CUDA 13 (driver 580 or later). Observed 2026-11-18.
driver_major="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -n 1 | cut -d. -f1)"
if [ "${driver_major:-0}" -lt 580 ]; then
    echo "bootstrap: guest driver $driver_major is older than 580; installing nvidia-driver-580"
    apt_install "linux-headers-$(uname -r)" nvidia-driver-580
    echo "bootstrap: reboot the VM and run this script again" >&2
    exit 3
fi

if ! command -v docker >/dev/null; then
    apt_install docker.io
    systemctl enable --now docker
fi
if ! docker compose version >/dev/null 2>&1; then
    apt_install docker-compose-v2 || apt_install docker-compose-plugin
fi
if ! docker info --format '{{json .Runtimes}}' | grep -q nvidia; then
    keyring=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | gpg --batch --yes --dearmor -o "$keyring"
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed "s#deb https://#deb [signed-by=$keyring] https://#g" > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update -q >/dev/null
    apt_install nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
fi

nvidia-smi --query-gpu=name,compute_cap,driver_version,memory.total --format=csv,noheader
docker compose version
docker run --rm --gpus all ubuntu:22.04 nvidia-smi -L
echo "bootstrap: ready"
