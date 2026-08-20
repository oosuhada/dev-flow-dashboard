#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIVE_ROOT="${DEV_FLOW_LIVE_ROOT:-$HOME/services/dev-flow-dashboard}"
SERVICE_LABEL="${DEV_FLOW_SERVICE_LABEL:-com.oosu.dev-flow-dashboard}"
HEALTH_URL="${DEV_FLOW_HEALTH_URL:-http://127.0.0.1:4310/dev_dashboard/api/health}"
BACKUP_ROOT="$LIVE_ROOT/.deploy-prev"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "deploy-macmini.sh must run on macOS" >&2
  exit 1
fi

if [[ ! -d "$LIVE_ROOT/.venv" || ! -f "$LIVE_ROOT/.env" ]]; then
  echo "live runtime is not initialized at $LIVE_ROOT" >&2
  exit 1
fi

if [[ ! -f "$ROOT/frontend/dist/index.html" ]]; then
  echo "frontend build is missing: $ROOT/frontend/dist/index.html" >&2
  exit 1
fi

echo "Deploying $(git -C "$ROOT" rev-parse --short HEAD) to $LIVE_ROOT"

rm -rf "$BACKUP_ROOT"
mkdir -p "$BACKUP_ROOT"
rsync -a \
  --exclude '.deploy-prev/' \
  --exclude '.env' \
  --exclude '.state/' \
  --exclude '.venv/' \
  --exclude 'frontend/node_modules/' \
  "$LIVE_ROOT/" "$BACKUP_ROOT/"

# Preserve runtime-only state and credentials while syncing tracked application
# source. node_modules and dist are produced by the workflow and handled
# separately below.
rsync -a --delete \
  --exclude '.git/' \
  --exclude '.github/' \
  --exclude '.env' \
  --exclude '.state/' \
  --exclude '.venv/' \
  --exclude '.deploy-prev/' \
  --exclude 'frontend/node_modules/' \
  --exclude 'frontend/dist/' \
  "$ROOT/" "$LIVE_ROOT/"

DIST_NEXT="$LIVE_ROOT/frontend/dist.next"
DIST_PREV="$LIVE_ROOT/frontend/dist.prev"
rm -rf "$DIST_NEXT" "$DIST_PREV"
mkdir -p "$DIST_NEXT"
rsync -a "$ROOT/frontend/dist/" "$DIST_NEXT/"

if [[ -d "$LIVE_ROOT/frontend/dist" ]]; then
  mv "$LIVE_ROOT/frontend/dist" "$DIST_PREV"
fi
mv "$DIST_NEXT" "$LIVE_ROOT/frontend/dist"

# Keep the persistent runtime venv in sync with source requirements. This is
# intentionally quiet and idempotent for normal frontend-only deploys.
"$LIVE_ROOT/.venv/bin/python" -m pip install -q -r "$LIVE_ROOT/backend/requirements.txt"

USER_ID="$(id -u)"
launchctl kickstart -k "gui/$USER_ID/$SERVICE_LABEL"

for attempt in {1..30}; do
  if curl -fsS "$HEALTH_URL" >/tmp/dev-flow-dashboard-health.json 2>/dev/null; then
    if grep -q '"status":"ok"' /tmp/dev-flow-dashboard-health.json; then
      echo "Health check passed"
      rm -rf "$DIST_PREV"
      rm -rf "$BACKUP_ROOT"
      exit 0
    fi
  fi
  sleep 1
done

echo "Health check failed after deploy" >&2
if [[ -d "$BACKUP_ROOT" ]]; then
  rsync -a --delete \
    --exclude '.deploy-prev/' \
    --exclude '.env' \
    --exclude '.state/' \
    --exclude '.venv/' \
    --exclude 'frontend/node_modules/' \
    "$BACKUP_ROOT/" "$LIVE_ROOT/"
  launchctl kickstart -k "gui/$USER_ID/$SERVICE_LABEL" || true
  rm -rf "$BACKUP_ROOT"
  echo "Restored previous application source" >&2
fi
exit 1

