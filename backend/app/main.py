from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .ai import ai_advisor
from .events import DashboardEvent, event_hub
from .github import GitHubAggregator, configured_repositories

DASHBOARD_PREFIX = "/dev_dashboard"
app = FastAPI(
    title="Dev Flow Dashboard API",
    docs_url=f"{DASHBOARD_PREFIX}/api/docs",
    openapi_url=f"{DASHBOARD_PREFIX}/api/openapi.json",
)
aggregator = GitHubAggregator()
_watch_task: asyncio.Task[None] | None = None
_background_tasks: set[asyncio.Task[None]] = set()
_ci_ai_tasks: dict[str, asyncio.Task[None]] = {}


def _watch_interval_seconds() -> int:
    try:
        return max(30, min(600, int(os.getenv("GITHUB_WATCH_INTERVAL_SECONDS", "120"))))
    except ValueError:
        return 120


def _webhook_secret() -> str:
    return os.getenv("GITHUB_WEBHOOK_SECRET", "")


def _verify_webhook(body: bytes, signature: str | None) -> bool:
    secret = _webhook_secret()
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, f"sha256={digest}")


async def _watch_github() -> None:
    fingerprints: dict[str, str] = {}
    while True:
        for repo in configured_repositories():
            try:
                current = await aggregator.repository_fingerprint(repo)
                previous = fingerprints.get(repo)
                fingerprints[repo] = current
                if previous is not None and current != previous:
                    aggregator.cache.clear(repo)
                    event_hub.publish(DashboardEvent(repo=repo, event="repository_changed"))
                    _schedule_ai(repo, "repository_changed", "fallback-watch", None)
            except (RuntimeError, ValueError):
                # A transient GitHub error must not kill the watcher; the next
                # interval retries and the browser also reconnects SSE itself.
                pass
        await asyncio.sleep(_watch_interval_seconds())


async def _run_ai_analysis(repo: str, event_name: str, action: str | None, number: int | None) -> None:
    if not ai_advisor.available:
        return
    try:
        # GitHub can emit the webhook a fraction before all related REST
        # resources settle. A short delay keeps the AI input coherent while
        # still feeling immediate in the dashboard.
        if event_name not in {"startup", "repository_changed"}:
            await asyncio.sleep(0.8)
        snapshot_data = await aggregator.snapshot(repo, force=True)
        pull_detail_data = None
        if number is not None:
            try:
                pull_detail_data = await aggregator.pull_detail(repo, number)
            except (RuntimeError, ValueError):
                pull_detail_data = None
        await ai_advisor.analyze_repository(
            repo,
            snapshot_data,
            {"event": event_name, "action": action, "number": number},
            pull_detail_data,
        )
        event_hub.publish(DashboardEvent(repo=repo, event="ai_analysis", action="completed", number=number))
    except Exception:
        # AI is advisory. A provider/API failure must never break GitHub event
        # ingestion or the deterministic dashboard.
        event_hub.publish(DashboardEvent(repo=repo, event="ai_analysis", action="failed", number=number))


def _schedule_ai(repo: str, event_name: str, action: str | None, number: int | None) -> None:
    if not ai_advisor.available:
        return
    if event_name in {"check_run", "check_suite", "workflow_run"}:
        previous = _ci_ai_tasks.get(repo)
        if previous is not None and not previous.done():
            previous.cancel()

        async def after_ci_burst() -> None:
            try:
                await asyncio.sleep(2.5)
                await _run_ai_analysis(repo, "ci_status_changed", f"{event_name}:{action or 'updated'}", number)
            except asyncio.CancelledError:
                return

        task = asyncio.create_task(after_ci_burst())
        _ci_ai_tasks[repo] = task
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return
    task = asyncio.create_task(_run_ai_analysis(repo, event_name, action, number))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@app.on_event("startup")
async def start_github_watcher() -> None:
    global _watch_task
    if _watch_task is None or _watch_task.done():
        _watch_task = asyncio.create_task(_watch_github())
    for repo in configured_repositories():
        _schedule_ai(repo, "startup", "initial-sync", None)


@app.on_event("shutdown")
async def stop_github_watcher() -> None:
    global _watch_task
    if _watch_task is not None:
        _watch_task.cancel()
        try:
            await _watch_task
        except asyncio.CancelledError:
            pass
        _watch_task = None
    tasks = list(_background_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    _background_tasks.clear()
    _ci_ai_tasks.clear()


@app.get("/api/health")
@app.get(f"{DASHBOARD_PREFIX}/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "repositories": configured_repositories(),
        "githubAuthentication": "authenticated" if aggregator.authenticated else "public",
        "cacheTtlSeconds": aggregator.cache.ttl_seconds,
        "liveUpdates": "github-webhook+sse",
        "watcherFallbackSeconds": _watch_interval_seconds(),
        "ai": {
            "enabled": ai_advisor.available,
            "model": ai_advisor.model,
            "project": ai_advisor.project if ai_advisor.available else None,
            "triggerMode": ai_advisor.trigger_mode,
        },
    }


@app.get("/api/repositories")
@app.get(f"{DASHBOARD_PREFIX}/api/repositories")
async def repositories() -> dict[str, list[str]]:
    return {"repositories": configured_repositories()}


@app.get("/api/events")
@app.get(f"{DASHBOARD_PREFIX}/api/events")
async def events(repo: str = Query(...)) -> StreamingResponse:
    if repo not in configured_repositories():
        raise HTTPException(status_code=400, detail="Repository is not configured")

    async def stream():
        queue = event_hub.subscribe()
        try:
            yield "retry: 2000\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event.repo != repo:
                    continue
                channel = "ai" if event.event == "ai_analysis" else "github"
                yield f"event: {channel}\ndata: {event.encode()}\n\n"
        finally:
            event_hub.unsubscribe(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/github/webhook")
@app.post(f"{DASHBOARD_PREFIX}/api/github/webhook")
async def github_webhook(request: Request) -> dict[str, object]:
    body = await request.body()
    if not _verify_webhook(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc

    repo = str(((payload.get("repository") or {}).get("full_name") or ""))
    if repo not in configured_repositories():
        return {"accepted": False, "reason": "repository-not-configured"}

    event_name = request.headers.get("X-GitHub-Event", "unknown")
    action = payload.get("action")
    number = payload.get("number")
    if not isinstance(number, int):
        issue = payload.get("issue") or {}
        pull_request = payload.get("pull_request") or {}
        number = issue.get("number") or pull_request.get("number")
        if not isinstance(number, int):
            number = None

    aggregator.cache.clear(repo)
    event_hub.publish(DashboardEvent(repo=repo, event=event_name, action=action, number=number))
    if event_name != "ping":
        _schedule_ai(repo, event_name, action, number)
    return {"accepted": True, "repo": repo, "event": event_name, "number": number}


@app.get("/api/ai/priority")
@app.get(f"{DASHBOARD_PREFIX}/api/ai/priority")
async def ai_priority(repo: str = Query(...), force: bool = Query(False)) -> dict[str, object]:
    if repo not in configured_repositories():
        raise HTTPException(status_code=400, detail="Repository is not configured")
    if not ai_advisor.available:
        return {"status": "disabled", "repository": repo, "model": ai_advisor.model}
    current = ai_advisor.state(repo)
    if force or current is None:
        try:
            snapshot_data = await aggregator.snapshot(repo, force=force)
            current = await ai_advisor.analyze_repository(
                repo,
                snapshot_data,
                {"event": "manual", "action": "force" if force else "initial", "number": None},
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return current or {"status": "analyzing", "repository": repo, "model": ai_advisor.model}


@app.get("/api/ai/commit")
@app.get(f"{DASHBOARD_PREFIX}/api/ai/commit")
async def ai_commit(repo: str = Query(...), sha: str = Query(...)) -> dict[str, object]:
    if not ai_advisor.available:
        return {"status": "disabled", "repository": repo, "sha": sha, "model": ai_advisor.model}
    try:
        commit_data, snapshot_data = await asyncio.gather(
            aggregator.commit_detail(repo, sha),
            aggregator.snapshot(repo),
        )
        return await ai_advisor.analyze_commit(repo, commit_data, snapshot_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/snapshot")
@app.get(f"{DASHBOARD_PREFIX}/api/snapshot")
async def snapshot(repo: str = Query(...), force: bool = Query(False)) -> dict[str, object]:
    try:
        return await aggregator.snapshot(repo, force=force)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/commit")
@app.get(f"{DASHBOARD_PREFIX}/api/commit")
async def commit(repo: str = Query(...), sha: str = Query(...)) -> dict[str, object]:
    try:
        return await aggregator.commit_detail(repo, sha)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/pull")
@app.get(f"{DASHBOARD_PREFIX}/api/pull")
async def pull(repo: str = Query(...), number: int = Query(...)) -> dict[str, object]:
    try:
        return await aggregator.pull_detail(repo, number)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/file")
@app.get(f"{DASHBOARD_PREFIX}/api/file")
async def file_content(repo: str = Query(...), sha: str = Query(...), path: str = Query(...)) -> dict[str, object]:
    try:
        return await aggregator.file_content(repo, sha, path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
ASSETS = DIST / "assets"
if ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=ASSETS), name="assets")
    app.mount(f"{DASHBOARD_PREFIX}/assets", StaticFiles(directory=ASSETS), name="dashboard-assets")


@app.get("/{path:path}", include_in_schema=False)
async def frontend(path: str):
    index = DIST / "index.html"
    if index.exists():
        requested = DIST / path
        if path and requested.is_file() and requested.resolve().is_relative_to(DIST.resolve()):
            return FileResponse(requested)
        return FileResponse(index)
    raise HTTPException(status_code=404, detail="Frontend build not found")
