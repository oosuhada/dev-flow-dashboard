# Dev Flow Dashboard

Standalone, read-only GitHub development-flow observability dashboard.

It visualizes open pull-request dependencies, downstream bottlenecks, reviews/checks,
and recent commit topology for configured repositories. GitHub credentials remain on
the server; the browser only talks to `/api/*`.

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

