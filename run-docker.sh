#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$script_dir"

if docker compose version >/dev/null 2>&1; then
  # Docker Compose v2 plugin (current Docker releases).
  compose_command=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  # Docker Compose v1 standalone binary (common on older Linux servers).
  compose_command=(docker-compose)
else
  echo "Docker Compose is not installed. Install the Compose plugin or docker-compose." >&2
  exit 1
fi

if [[ "${1:-}" == "--stop" ]]; then
  "${compose_command[@]}" down
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

"${compose_command[@]}" up --build --detach
echo "Variational skew monitor is running."
echo "View logs: ${compose_command[*]} logs --follow"
