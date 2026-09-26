#!/usr/bin/env bash
# Start UPSC Notes Studio in Docker, using a GPU when this computer has one and the CPU otherwise.
#
#   ./start.sh            detect the hardware and start
#   ./start.sh down       stop everything
#
# Detection order (override with UPSC_NOTES_ACCEL=nvidia|amd|host|cpu):
#   macOS  : Ollama installed on the Mac → use it (Apple GPU / Metal). Docker cannot reach the Apple GPU.
#   NVIDIA : nvidia-smi works and Docker can pass the GPU through → bundled Ollama on the GPU.
#   AMD    : /dev/kfd exists (Linux ROCm) → bundled Ollama (ROCm image) on the GPU.
#   else   : bundled Ollama on the CPU.
# The model follows the hardware (GPU: qwen2.5:7b, CPU: qwen2.5:3b) unless UPSC_NOTES_MODEL is set.
set -euo pipefail
cd "$(dirname "$0")"

if [[ "${1:-}" == "down" ]]; then
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.amd.yml down 2>/dev/null \
    || docker compose down
  exit 0
fi

command -v docker >/dev/null || { echo "Docker is not installed: https://docs.docker.com/get-docker/"; exit 1; }
docker info >/dev/null 2>&1 || { echo "Docker is not running. Start Docker Desktop (or the docker service) and retry."; exit 1; }

host_ollama() { curl -fs --max-time 2 http://localhost:11434/api/tags >/dev/null 2>&1; }
nvidia_ok() {
  command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1 \
    && docker run --rm --gpus all busybox true >/dev/null 2>&1
}

accel="${UPSC_NOTES_ACCEL:-}"
if [[ -z "$accel" ]]; then
  if [[ "$(uname -s)" == "Darwin" ]] && host_ollama; then accel=host
  elif nvidia_ok; then accel=nvidia
  elif [[ -e /dev/kfd && -e /dev/dri ]]; then accel=amd
  else accel=cpu
  fi
fi

case "$accel" in
  cpu)    model_default=qwen2.5:3b ;;
  *)      model_default=qwen2.5:7b ;;
esac
export UPSC_NOTES_MODEL="${UPSC_NOTES_MODEL:-$model_default}"

files=(-f docker-compose.yml)
services=()
case "$accel" in
  host)
    echo "Using Ollama on this Mac (Apple GPU)."
    files+=(-f docker-compose.host-ollama.yml); services=(upsc-notes)
    for m in "$UPSC_NOTES_MODEL" nomic-embed-text; do
      if command -v ollama >/dev/null; then ollama pull "$m" >/dev/null && echo "  model ready: $m"; fi
    done ;;
  nvidia) echo "Using the NVIDIA GPU."; files+=(-f docker-compose.gpu.yml) ;;
  amd)    echo "Using the AMD GPU (ROCm)."; files+=(-f docker-compose.amd.yml) ;;
  cpu)
    echo "No usable GPU found: running on the CPU."
    if [[ "$(uname -s)" == "Darwin" && -z "${UPSC_NOTES_ACCEL:-}" ]]; then
      echo "  Tip: install Ollama on the Mac (https://ollama.com) and rerun to use the Apple GPU."
    fi ;;
  *) echo "Unknown UPSC_NOTES_ACCEL=$accel (use nvidia, amd, host or cpu)"; exit 1 ;;
esac
echo "Model: $UPSC_NOTES_MODEL (first start downloads it)"

docker compose "${files[@]}" up -d --build ${services[@]+"${services[@]}"}
echo "Waiting for the app…"
for _ in $(seq 1 180); do
  if curl -fs http://127.0.0.1:${UPSC_NOTES_PORT:-8765}/api/status >/dev/null 2>&1; then
    echo "Ready: http://127.0.0.1:${UPSC_NOTES_PORT:-8765}"; exit 0
  fi
  sleep 5
done
echo "Still starting (model download can take a while). Check: docker compose logs -f"
