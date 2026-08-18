#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# macOS launch agents can inherit a low per-process descriptor limit. This
# service keeps SSE clients open while receiving GitHub webhook bursts, so the
# default can be exhausted even though individual requests close correctly.
ulimit -n "${NOFILE_LIMIT:-4096}" 2>/dev/null || true

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

exec "$ROOT/.venv/bin/uvicorn" backend.app.main:app --host 127.0.0.1 --port "${PORT:-4310}"

