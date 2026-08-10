#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$ROOT/.venv/bin/uvicorn" backend.app.main:app --host 127.0.0.1 --port "${PORT:-4310}"

