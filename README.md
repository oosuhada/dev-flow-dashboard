# Dev Flow Dashboard

Standalone, read-only GitHub development-flow observability and AI operations dashboard.

It visualizes open pull-request dependencies, downstream bottlenecks, reviews/checks,
and recent commit topology for configured repositories. GitHub credentials remain on
the server; the browser only talks to `/api/*`.

The deterministic Git/GitHub layer remains the source of truth for commit topology,
PR relationships, reviews, checks, and merge state. When `DEV_FLOW_AI_ENABLED=true`,
Vertex AI Gemini adds a persistent interpretation layer on top of those facts:

- every PR/review/comment/push webhook re-evaluates the open-PR priority queue;
- noisy CI webhook bursts are coalesced briefly so AI stays current instead of queuing stale runs;
- the previous repository analysis and recent triggers are persisted under `.state/ai/`
  and supplied to the next model call as rolling context;
- selecting a commit runs contextual impact analysis against the current open-PR flow;
- completed AI analyses are pushed to open browsers through the same SSE live-update channel.

The AI layer is advisory and never mutates GitHub state.

## Local development

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements-dev.txt
cd frontend && npm install && npm run build && cd ..
GITHUB_REPOSITORIES=Biz-CollabCraft/ontology_dashboard,Biz-CollabCraft/gen_data \
  .venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 4310
```

Optionally set `GITHUB_TOKEN` server-side for authenticated API limits.

## Docker

```bash
cp .env.example .env
docker compose up -d --build
```

The service is published only on `127.0.0.1:4310`, suitable for Cloudflare Tunnel.

