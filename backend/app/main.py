from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .github import GitHubAggregator, configured_repositories

app = FastAPI(title="Dev Flow Dashboard API", docs_url="/api/docs", openapi_url="/api/openapi.json")
aggregator = GitHubAggregator()


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "repositories": configured_repositories(),
        "githubAuthentication": "authenticated" if aggregator.authenticated else "public",
        "cacheTtlSeconds": aggregator.cache.ttl_seconds,
    }


@app.get("/api/repositories")
async def repositories() -> dict[str, list[str]]:
    return {"repositories": configured_repositories()}


@app.get("/api/snapshot")
async def snapshot(repo: str = Query(...), force: bool = Query(False)) -> dict[str, object]:
    try:
        return await aggregator.snapshot(repo, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
ASSETS = DIST / "assets"
if ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")


@app.get("/{path:path}", include_in_schema=False)
async def frontend(path: str):
    index = DIST / "index.html"
    if index.exists():
        requested = DIST / path
        if path and requested.is_file() and requested.resolve().is_relative_to(DIST.resolve()):
            return FileResponse(requested)
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend build not found")

