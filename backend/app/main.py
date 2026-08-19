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

from .activity import activity_store
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
_project_debounce_tasks: dict[tuple[str, str, int | None], asyncio.Task[None]] = {}
_project_ai_lock = asyncio.Lock()

PROJECT_CONTEXT_REPO = os.getenv("DEV_FLOW_PROJECT_CONTEXT_REPO", "Biz-CollabCraft/ontology_dashboard")
PROJECT_CONTEXT_DOCS = [
    "docs/final_team_role_and_step_plan.md",
    "docs/mvp/README.md",
    "docs/mvp/requirements-specification.md",
    "docs/mvp/current-mvp-implementation-baseline.md",
    "docs/architecture.md",
    "docs/closed-loop-product-consumption-contract.md",
]


def _watch_interval_seconds() -> int:
    try:
        return max(300, min(1800, int(os.getenv("GITHUB_WATCH_INTERVAL_SECONDS", "600"))))
    except ValueError:
        return 600


def _ai_debounce_seconds() -> int:
    try:
        return max(180, min(300, int(os.getenv("DEV_FLOW_AI_DEBOUNCE_SECONDS", "240"))))
    except ValueError:
        return 240


def _webhook_secret() -> str:
    return os.getenv("GITHUB_WEBHOOK_SECRET", "")


def _verify_webhook(body: bytes, signature: str | None) -> bool:
    secret = _webhook_secret()
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, f"sha256={digest}")


def _webhook_sender_is_bot(payload: dict[str, object]) -> bool:
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    login = str(sender.get("login") or "").lower()
    sender_type = str(sender.get("type") or "").lower()
    return sender_type == "bot" or login.endswith("[bot]") or login in {
        "vercel",
        "vercel[bot]",
        "github-actions",
        "github-actions[bot]",
    }


def _technical_comment(body: object) -> bool:
    text = str(body or "").strip()
    if not text:
        return False
    lower = text.lower()
    if lower in {
        "lgtm",
        "approve",
        "approved",
        "확인",
        "확인했습니다",
        "감사합니다",
        "좋습니다",
        "넵",
        "네",
        "ok",
        "okay",
    }:
        return False
    technical_tokens = (
        "[p0]",
        "[p1]",
        "[p2]",
        "[p3]",
        "bug",
        "error",
        "fail",
        "regression",
        "architecture",
        "contract",
        "migration",
        "api",
        "database",
        "db",
        "test",
        "code",
        "버그",
        "오류",
        "실패",
        "회귀",
        "아키텍처",
        "계약",
        "마이그레이션",
        "테스트",
        "코드",
        "수정",
        "구현",
        "변경",
        "누락",
        "위반",
        "왜 ",
        "어떻게",
        "?",
    )
    return any(token in lower for token in technical_tokens)


def _should_analyze_webhook(
    payload: dict[str, object], event_name: str, action: str | None
) -> bool:
    """Keep deterministic live updates broad while keeping Vertex triggers narrow."""

    if event_name in {"check_run", "check_suite", "workflow_run"}:
        return True
    if _webhook_sender_is_bot(payload):
        return False
    if event_name == "pull_request":
        return action in {
            "opened",
            "reopened",
            "edited",
            "synchronize",
            "ready_for_review",
            "closed",
        }
    if event_name == "pull_request_review":
        review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
        state = str(review.get("state") or "").lower()
        return action == "submitted" and (
            state in {"approved", "changes_requested"}
            or (state == "commented" and _technical_comment(review.get("body")))
        )
    if event_name in {"issue_comment", "pull_request_review_comment"}:
        comment = payload.get("comment") if isinstance(payload.get("comment"), dict) else {}
        return action == "created" and _technical_comment(comment.get("body"))
    return False


def _webhook_pm_context(payload: dict[str, object], event_name: str, action: str | None, number: int | None) -> dict[str, object]:
    """Keep the triggering GitHub evidence available even if REST refresh fails.

    The webhook is the freshest source for a newly-created comment/review.  PM
    analysis must not silently lose that evidence because a follow-up GitHub
    REST request is rate-limited or has not settled yet.
    """
    sender = payload.get("sender") if isinstance(payload.get("sender"), dict) else {}
    pull = payload.get("pull_request") if isinstance(payload.get("pull_request"), dict) else {}
    issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    comment = payload.get("comment") if isinstance(payload.get("comment"), dict) else {}
    check_run = payload.get("check_run") if isinstance(payload.get("check_run"), dict) else {}
    workflow_run = payload.get("workflow_run") if isinstance(payload.get("workflow_run"), dict) else {}

    def clipped(value: object, limit: int = 8000) -> str | None:
        text = str(value or "").strip()
        return text[:limit] if text else None

    subject = pull or issue
    context: dict[str, object] = {
        "event": event_name,
        "action": action,
        "number": number,
        "actor": clipped(sender.get("login"), 120),
        "title": clipped(subject.get("title"), 500),
    }
    if event_name == "pull_request_review":
        context.update({
            "reviewState": clipped(review.get("state"), 80),
            "reviewBody": clipped(review.get("body"), 12000),
            "headSha": clipped(((pull.get("head") or {}).get("sha") if isinstance(pull.get("head"), dict) else None), 64),
        })
    elif event_name in {"issue_comment", "pull_request_review_comment"}:
        context.update({
            "commentBody": clipped(comment.get("body"), 12000),
            "commentUrl": clipped(comment.get("html_url"), 1000),
        })
    elif event_name == "pull_request":
        context.update({
            "body": clipped(pull.get("body"), 8000),
            "headSha": clipped(((pull.get("head") or {}).get("sha") if isinstance(pull.get("head"), dict) else None), 64),
            "mergeableState": clipped(pull.get("mergeable_state"), 120),
            "merged": bool(pull.get("merged")),
        })
    elif event_name == "check_run":
        context.update({"check": clipped(check_run.get("name"), 300), "status": clipped(check_run.get("conclusion") or check_run.get("status"), 120)})
    elif event_name == "workflow_run":
        context.update({"workflow": clipped(workflow_run.get("name"), 300), "status": clipped(workflow_run.get("conclusion") or workflow_run.get("status"), 120)})
    return {key: value for key, value in context.items() if value is not None}


async def _watch_github() -> None:
    fingerprints: dict[str, str] = {}
    while True:
        for repo in configured_repositories():
            try:
                current = await aggregator.repository_fingerprint(repo)
                previous = fingerprints.get(repo)
                fingerprints[repo] = current
                if previous is not None and current != previous:
                    # This is recovery for a missed webhook. Conditional GETs
                    # make unchanged resources return 304, while the resulting
                    # snapshot repairs any local state that drifted.
                    await aggregator.snapshot(repo, force=True)
                    event_hub.publish(DashboardEvent(repo=repo, event="repository_changed"))
                    _schedule_project_pm(repo, "repository_changed", "fallback-watch", None)
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
        snapshot_data = await aggregator.snapshot(repo)
        pull_detail_data = next(
            (pull for pull in snapshot_data.get("pulls", []) if pull.get("number") == number),
            None,
        )
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


async def _load_project_memory(
    force: bool = False,
    *,
    allow_paid_fallback: bool = True,
) -> dict[str, object]:
    current = ai_advisor.project_memory()
    if current is not None and not force:
        return current
    if PROJECT_CONTEXT_REPO not in configured_repositories():
        if current is not None:
            return current
        raise RuntimeError(f"Project context repository is not configured: {PROJECT_CONTEXT_REPO}")
    try:
        snapshot_data = await aggregator.snapshot(PROJECT_CONTEXT_REPO, force=force)
    except RuntimeError:
        if current is not None:
            return current
        raise
    ref = snapshot_data.get("headSha")
    if not isinstance(ref, str) or not ref:
        raise RuntimeError("Project context repository has no default-branch HEAD")

    async def load(path: str) -> tuple[str, str] | None:
        try:
            payload = await aggregator.file_content(PROJECT_CONTEXT_REPO, ref, path)
            return path, str(payload.get("content") or "")
        except (RuntimeError, ValueError):
            return None

    loaded = await asyncio.gather(*(load(path) for path in PROJECT_CONTEXT_DOCS))
    documents = {path: content for item in loaded if item is not None for path, content in [item]}
    if not documents:
        if current is not None:
            return current
        raise RuntimeError("No canonical project context documents could be loaded")
    return await ai_advisor.ensure_project_memory(
        documents,
        allow_paid_fallback=allow_paid_fallback,
    )


async def _run_project_pm(
    trigger_repo: str,
    event_name: str,
    action: str | None,
    number: int | None,
    *,
    refresh_docs: bool = False,
    event_context: dict[str, object] | None = None,
) -> None:
    if not ai_advisor.available:
        return
    async with _project_ai_lock:
        try:
            trigger = {"repository": trigger_repo, "event": event_name, "action": action, "number": number}
            if event_context:
                trigger["eventContext"] = event_context
            selected_model, _thinking_level = ai_advisor.project_model_for(trigger)
            if not ai_advisor.auto_pm_allowed(selected_model):
                for repo in configured_repositories():
                    event_hub.publish(
                        DashboardEvent(repo=repo, event="ai_project", action="budget-exhausted", number=number)
                    )
                return
            if event_name not in {"startup", "repository_changed"}:
                await asyncio.sleep(0.6)
            memory = await _load_project_memory(
                force=refresh_docs,
                allow_paid_fallback=ai_advisor.paid_auto_allowed(),
            )
            snapshots: dict[str, dict[str, object]] = {}
            for repo in configured_repositories():
                try:
                    snapshots[repo] = await aggregator.snapshot(repo)
                except RuntimeError:
                    stale = aggregator.stale_snapshot(repo)
                    if stale is None:
                        raise
                    snapshots[repo] = stale
            semantic_revision = ai_advisor.project_semantic_revision(snapshots, memory)
            current_state = ai_advisor.project_state() or {}
            if event_name in {"startup", "repository_changed", "ci_status_changed"} and (
                current_state.get("semanticRevision") == semantic_revision
            ):
                for repo in configured_repositories():
                    event_hub.publish(
                        DashboardEvent(repo=repo, event="ai_project", action="unchanged-skip", number=number)
                    )
                return
            pull_detail_data = next(
                (pull for pull in snapshots.get(trigger_repo, {}).get("pulls", []) if pull.get("number") == number),
                None,
            )
            project_state = await ai_advisor.analyze_project(
                snapshots,
                trigger,
                memory,
                pull_detail_data,
                allow_paid_fallback=ai_advisor.paid_auto_allowed(),
            )
            budget = ai_advisor.record_auto_pm_usage(project_state.get("usage"))
            snapshot_id = activity_store.add_pm_snapshot(project_state)
            activity_store.add(
                source="ai_pm",
                repository=trigger_repo,
                event="ai_project",
                action="completed",
                number=number,
                actor=str(project_state.get("model") or ai_advisor.model),
                title=str(project_state.get("headline") or "AI Project Manager updated"),
                summary=" · ".join(str(item) for item in (project_state.get("changesSinceLast") or [])[:3]) or str(project_state.get("currentObjective") or ""),
                metadata={
                    "projectHealth": project_state.get("projectHealth"),
                    "analysisSequence": project_state.get("analysisSequence"),
                    "pmSnapshotId": snapshot_id,
                    "trigger": project_state.get("trigger"),
                    "budget": budget,
                },
            )
            # Project PM is cross-repository. Broadcast completion to every open
            # repository SSE subscription so the always-on console refreshes
            # even when the triggering event happened in another repo.
            for repo in configured_repositories():
                event_hub.publish(DashboardEvent(repo=repo, event="ai_project", action="completed", number=number))
        except Exception:
            for repo in configured_repositories():
                event_hub.publish(DashboardEvent(repo=repo, event="ai_project", action="failed", number=number))


def _schedule_project_pm(
    repo: str,
    event_name: str,
    action: str | None,
    number: int | None,
    event_context: dict[str, object] | None = None,
) -> None:
    if not ai_advisor.available:
        return
    debounce_kind: str | None = None
    normalized_event = event_name
    normalized_action = action
    if event_name in {"check_run", "check_suite", "workflow_run"}:
        if number is None:
            return
        # PR synchronize/edit and its follow-on CI belong to one semantic
        # state transition. Sharing a key makes each later CI event extend the
        # same quiet window instead of paying once for the push and again for
        # the final checks.
        debounce_kind = "pr-state"
        normalized_event = "ci_status_changed"
        normalized_action = f"{event_name}:{action or 'updated'}"
    elif event_name == "pull_request" and action in {"edited", "synchronize"}:
        debounce_kind = "pr-state"
    elif event_name == "repository_changed":
        debounce_kind = "fallback"

    if debounce_kind is not None:
        key = (repo, debounce_kind, number)
        previous = _project_debounce_tasks.get(key)
        if previous is not None and not previous.done():
            previous.cancel()

        async def after_quiet_period() -> None:
            try:
                await asyncio.sleep(_ai_debounce_seconds())
                await _run_project_pm(
                    repo,
                    normalized_event,
                    normalized_action,
                    number,
                    event_context=event_context,
                )
            except asyncio.CancelledError:
                return
            finally:
                current = _project_debounce_tasks.get(key)
                if current is asyncio.current_task():
                    _project_debounce_tasks.pop(key, None)

        task = asyncio.create_task(after_quiet_period())
        _project_debounce_tasks[key] = task
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return

    refresh_docs = bool(
        event_name == "startup"
        or (
            repo == PROJECT_CONTEXT_REPO
            and event_name == "pull_request"
            and action == "closed"
            and (event_context or {}).get("merged")
        )
    )
    task = asyncio.create_task(
        _run_project_pm(repo, event_name, action, number, refresh_docs=refresh_docs, event_context=event_context)
    )
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


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


def _schedule_targeted_pull_refresh(repo: str, number: int) -> None:
    async def refresh_after_webhook_settles() -> None:
        try:
            await asyncio.sleep(0.8)
            refreshed = await aggregator.refresh_pull(repo, number)
            if refreshed is not None:
                event_hub.publish(
                    DashboardEvent(repo=repo, event="targeted_pull_refresh", action="completed", number=number)
                )
        except (RuntimeError, ValueError):
            # Webhook-patched state remains available. The conditional watcher
            # repairs any missed detail after backoff or a transient failure.
            return

    task = asyncio.create_task(refresh_after_webhook_settles())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


@app.on_event("startup")
async def start_github_watcher() -> None:
    global _watch_task
    if _watch_task is None or _watch_task.done():
        _watch_task = asyncio.create_task(_watch_github())
    current_project_state = ai_advisor.project_state()
    if current_project_state is not None:
        activity_store.add_pm_snapshot(current_project_state)
    if configured_repositories():
        _schedule_project_pm(PROJECT_CONTEXT_REPO if PROJECT_CONTEXT_REPO in configured_repositories() else configured_repositories()[0], "startup", "initial-sync", None)


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
    _project_debounce_tasks.clear()


@app.get("/api/health")
@app.get(f"{DASHBOARD_PREFIX}/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "repositories": configured_repositories(),
        "githubAuthentication": aggregator.authentication_mode,
        "githubRestCircuit": aggregator.rate_limit_status(),
        "cacheTtlSeconds": aggregator.cache.ttl_seconds,
        "liveUpdates": "github-webhook+sse",
        "watcherFallbackSeconds": _watch_interval_seconds(),
        "ai": {
            "enabled": ai_advisor.available,
            "model": ai_advisor.model,
            "reasoningModel": ai_advisor.reasoning_model,
            "simpleModel": ai_advisor.simple_model,
            "simpleProvider": (
                "gemini-developer-free"
                if ai_advisor.free_available
                else "vertex-ai"
            ),
            "freeTierEnabled": ai_advisor.free_available,
            "project": ai_advisor.project if ai_advisor.available else None,
            "triggerMode": ai_advisor.trigger_mode,
            "debounceSeconds": _ai_debounce_seconds(),
            "automaticBudget": ai_advisor.auto_budget_status(),
            "projectManager": True,
            "projectContextRepository": PROJECT_CONTEXT_REPO,
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
                channel = "activity" if event.event == "activity" else ("project" if event.event == "ai_project" else ("ai" if event.event == "ai_analysis" else "github"))
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
            event_payload = (
                payload.get("check_run")
                or payload.get("check_suite")
                or payload.get("workflow_run")
                or {}
            )
            pull_refs = event_payload.get("pull_requests") or []
            candidate = (pull_refs[0] if pull_refs else {}).get("number")
            number = candidate if isinstance(candidate, int) else None

    aggregator.apply_webhook(repo, event_name, payload, number)
    if event_name != "ping":
        activity_id = activity_store.add_github(repo, event_name, action, number, payload)
        # Notifications are project-wide. Broadcast to every open repository
        # subscription so a gen_data event is visible while ontology_dashboard
        # is selected (and vice versa).
        for subscribed_repo in configured_repositories():
            event_hub.publish(DashboardEvent(repo=subscribed_repo, event="activity", action=str(activity_id), number=number))
    event_hub.publish(DashboardEvent(repo=repo, event=event_name, action=action, number=number))
    if event_name == "pull_request" and number is not None and action in {"opened", "reopened", "synchronize"}:
        _schedule_targeted_pull_refresh(repo, number)
    if event_name != "ping" and _should_analyze_webhook(payload, event_name, action):
        _schedule_project_pm(repo, event_name, action, number, _webhook_pm_context(payload, event_name, action, number))
    return {"accepted": True, "repo": repo, "event": event_name, "number": number}


@app.get("/api/activity")
@app.get(f"{DASHBOARD_PREFIX}/api/activity")
async def activity(limit: int = Query(300, ge=1, le=1000), repo: str | None = Query(None)) -> dict[str, object]:
    if repo is not None and repo not in configured_repositories():
        raise HTTPException(status_code=400, detail="Repository is not configured")
    items = activity_store.list(limit=limit, repository=repo)
    return {"items": items, "count": len(items)}


@app.get("/api/ai/project-history")
@app.get(f"{DASHBOARD_PREFIX}/api/ai/project-history")
async def ai_project_history(limit: int = Query(100, ge=1, le=500)) -> dict[str, object]:
    items = activity_store.list_pm_snapshots(limit=limit)
    return {"items": items, "count": len(items)}


@app.get("/api/ai/project-history/{snapshot_id}")
@app.get(f"{DASHBOARD_PREFIX}/api/ai/project-history/{{snapshot_id}}")
async def ai_project_history_detail(snapshot_id: int) -> dict[str, object]:
    state = activity_store.get_pm_snapshot(snapshot_id)
    if state is None:
        raise HTTPException(status_code=404, detail="PM snapshot not found")
    return state


@app.get("/api/ai/project")
@app.get(f"{DASHBOARD_PREFIX}/api/ai/project")
async def ai_project(force: bool = Query(False)) -> dict[str, object]:
    if not ai_advisor.available:
        return {"status": "disabled", "model": ai_advisor.model}
    current = ai_advisor.project_state()
    if force or current is None:
        try:
            memory = await _load_project_memory(force=False)
            snapshots: dict[str, dict[str, object]] = {}
            for repo in configured_repositories():
                try:
                    snapshots[repo] = await aggregator.snapshot(repo, force=force)
                except RuntimeError:
                    stale = aggregator.stale_snapshot(repo)
                    if stale is None:
                        raise
                    snapshots[repo] = stale
            current = await ai_advisor.analyze_project(
                snapshots,
                {"repository": "project", "event": "manual", "action": "force" if force else "initial", "number": None},
                memory,
            )
            activity_store.add_pm_snapshot(current)
        except RuntimeError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return current or {"status": "analyzing", "model": ai_advisor.model}


@app.get("/api/ai/project-memory")
@app.get(f"{DASHBOARD_PREFIX}/api/ai/project-memory")
async def ai_project_memory() -> dict[str, object]:
    if not ai_advisor.available:
        return {"status": "disabled", "model": ai_advisor.model}
    try:
        return await _load_project_memory()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/api/ai/chat")
@app.post(f"{DASHBOARD_PREFIX}/api/ai/chat")
async def ai_chat(request: Request) -> dict[str, object]:
    if not ai_advisor.available:
        return {"status": "disabled", "model": ai_advisor.model}
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON") from exc
    question = str(payload.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    raw_history = payload.get("history") or []
    history = [
        {"role": str(item.get("role") or "user"), "content": str(item.get("content") or "")[:6000]}
        for item in raw_history[-12:]
        if isinstance(item, dict)
    ]
    try:
        memory = await _load_project_memory()
        current = ai_advisor.project_state()
        return await ai_advisor.chat_project(question, history, memory, current)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


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
        stale = aggregator.stale_snapshot(repo)
        if stale is not None:
            return {**stale, "warning": str(exc)}
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
