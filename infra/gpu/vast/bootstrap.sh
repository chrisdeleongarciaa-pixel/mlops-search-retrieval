#!/usr/bin/env bash
# Prepare a vast.ai Ubuntu 22.04 VM (docker.io/vastai/kvm:ubuntu_terminal) for run-all.sh, as root.
#
#   bash infra/gpu/vast/bootstrap.sh
#
# Installs what is missing: git, curl, Docker with the compose plugin and the NVIDIA Container
# Toolkit (from NVIDIA's apt repository), then checks that a container sees the GPU. Idempotent.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

apt_install() { apt-get install -y -q --no-install-recommends "$@" >/dev/null; }
apt-get update -q >/dev/null
apt_install git curl ca-certificates gnupg python3 python3-pip util-linux

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
