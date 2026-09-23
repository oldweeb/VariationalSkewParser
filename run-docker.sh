#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

if [[ "${1:-}" == "--stop" ]]; then
  docker compose down
  exit 0
fi

if [[ -n "${1:-}" ]]; then
  echo "Usage: ./run-docker.sh [--stop]" >&2
  exit 2
fi

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and add the Telegram credentials." >&2
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running or is unavailable to this user." >&2
  exit 1
fi

docker compose up --build --detach
echo "Variational skew monitor is running."
echo "View logs: docker compose logs --follow"
