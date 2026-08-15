from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


def _clip(value: Any, limit: int = 280) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


class ActivityStore:
    """Small persistent event inbox for the always-on dashboard.

    GitHub webhook deliveries are append-only, so SQLite in WAL mode is enough
    here and avoids adding another network service to the Mac mini deployment.
    """

    def __init__(self) -> None:
        state_root = Path(os.getenv("DEV_FLOW_STATE_DIR", ".state"))
        state_root.mkdir(parents=True, exist_ok=True)
        self.path = Path(os.getenv("DEV_FLOW_ACTIVITY_DB", str(state_root / "activity.sqlite3")))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    source TEXT NOT NULL,
                    repository TEXT NOT NULL,
                    event TEXT NOT NULL,
                    action TEXT,
                    number INTEGER,
                    actor TEXT,
                    title TEXT NOT NULL,
                    summary TEXT,
                    url TEXT,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_activity_created_at ON activity(created_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_activity_repo ON activity(repository, created_at DESC)")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pm_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    generated_at TEXT,
                    model TEXT,
                    project_health TEXT,
                    step_number INTEGER,
                    step_name TEXT,
                    headline TEXT,
                    analysis_sequence INTEGER,
                    trigger_json TEXT NOT NULL DEFAULT '{}',
                    state_json TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_pm_snapshots_created_at ON pm_snapshots(created_at DESC)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_pm_snapshots_sequence ON pm_snapshots(analysis_sequence DESC)")

    def add(
        self,
        *,
        source: str,
        repository: str,
        event: str,
        title: str,
        action: str | None = None,
        number: int | None = None,
        actor: str | None = None,
        summary: str | None = None,
        url: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: float | None = None,
    ) -> int:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO activity (
                    created_at, source, repository, event, action, number,
                    actor, title, summary, url, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at or time.time(),
                    source,
                    repository,
                    event,
                    action,
                    number,
                    actor,
                    _clip(title, 220),
                    _clip(summary, 500) if summary else None,
                    url,
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            return int(cursor.lastrowid)

    def add_github(self, repository: str, event: str, action: str | None, number: int | None, payload: dict[str, Any]) -> int:
        sender = payload.get("sender") or {}
        actor = str(sender.get("login") or "github")
        pull = payload.get("pull_request") or {}
        issue = payload.get("issue") or {}
        review = payload.get("review") or {}
        comment = payload.get("comment") or {}
        check_run = payload.get("check_run") or {}
        check_suite = payload.get("check_suite") or {}
        workflow_run = payload.get("workflow_run") or {}

        subject = pull or issue
        subject_title = str(subject.get("title") or "")
        url = str(subject.get("html_url") or review.get("html_url") or comment.get("html_url") or "") or None

        if event == "pull_request":
            title = f"PR #{number} {action or 'updated'}"
            summary = subject_title
        elif event == "pull_request_review":
            state = str(review.get("state") or "reviewed").replace("_", " ")
            title = f"PR #{number} review · {state}"
            summary = str(review.get("body") or subject_title)
        elif event in {"pull_request_review_comment", "issue_comment"}:
            title = f"PR #{number} new comment"
            summary = str(comment.get("body") or subject_title)
        elif event == "push":
            ref = str(payload.get("ref") or "").removeprefix("refs/heads/")
            commits = payload.get("commits") or []
            title = f"Push · {ref or 'branch'}"
            summary = f"{len(commits)} commit(s) · {_clip((commits[-1] if commits else {}).get('message'), 180)}"
        elif event == "check_run":
            title = f"Check · {check_run.get('name') or 'run'}"
            summary = str(check_run.get("conclusion") or check_run.get("status") or action or "updated")
        elif event == "check_suite":
            suite = payload.get("check_suite") or {}
            title = "Check suite updated"
            summary = str(suite.get("conclusion") or suite.get("status") or action or "updated")
        elif event == "workflow_run":
            title = f"Workflow · {workflow_run.get('name') or 'run'}"
            summary = str(workflow_run.get("conclusion") or workflow_run.get("status") or action or "updated")
            url = str(workflow_run.get("html_url") or "") or url
        else:
            title = f"{event.replace('_', ' ')} · {action or 'updated'}"
            summary = subject_title or None

        sha = None
        ref = None
        if event == "push":
            sha = str(payload.get("after") or "") or None
            ref = str(payload.get("ref") or "").removeprefix("refs/heads/") or None
        elif event == "check_run":
            sha = str(check_run.get("head_sha") or "") or None
        elif event == "check_suite":
            sha = str(check_suite.get("head_sha") or "") or None
        elif event == "workflow_run":
            sha = str(workflow_run.get("head_sha") or "") or None
            ref = str(workflow_run.get("head_branch") or "") or None
        elif pull:
            sha = str(((pull.get("head") or {}).get("sha") or "")) or None

        return self.add(
            source="github",
            repository=repository,
            event=event,
            action=action,
            number=number,
            actor=actor,
            title=title,
            summary=summary,
            url=url,
            metadata={
                "deliveryType": event,
                "sha": sha,
                "ref": ref,
                "workflow": workflow_run.get("name") or check_run.get("name"),
            },
        )

    def list(self, *, limit: int = 300, repository: str | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        query = "SELECT * FROM activity"
        parameters: list[Any] = []
        if repository:
            query += " WHERE repository = ?"
            parameters.append(repository)
        query += " ORDER BY id DESC LIMIT ?"
        parameters.append(limit)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            result.append(
                {
                    "id": row["id"],
                    "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(row["created_at"])),
                    "source": row["source"],
                    "repository": row["repository"],
                    "event": row["event"],
                    "action": row["action"],
                    "number": row["number"],
                    "actor": row["actor"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "url": row["url"],
                    "metadata": metadata,
                }
            )
        return result

    def add_pm_snapshot(self, state: dict[str, Any]) -> int:
        current_step = state.get("currentStep") or {}
        generated_at = str(state.get("generatedAt") or "") or None
        created_at = time.time()
        if generated_at:
            try:
                from datetime import datetime

                created_at = datetime.fromisoformat(generated_at.replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        with self._lock, self._connect() as connection:
            existing = None
            if generated_at:
                existing = connection.execute(
                    "SELECT id FROM pm_snapshots WHERE generated_at = ? ORDER BY id DESC LIMIT 1",
                    (generated_at,),
                ).fetchone()
            if existing is not None:
                return int(existing["id"])
            cursor = connection.execute(
                """
                INSERT INTO pm_snapshots (
                    created_at, generated_at, model, project_health, step_number,
                    step_name, headline, analysis_sequence, trigger_json, state_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    created_at,
                    generated_at,
                    state.get("model"),
                    state.get("projectHealth"),
                    current_step.get("number"),
                    current_step.get("name"),
                    _clip(state.get("headline"), 500),
                    state.get("analysisSequence"),
                    json.dumps(state.get("trigger") or {}, ensure_ascii=False, separators=(",", ":")),
                    json.dumps(state, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            return int(cursor.lastrowid)

    def list_pm_snapshots(self, *, limit: int = 100) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 500))
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, created_at, generated_at, model, project_health,
                       step_number, step_name, headline, analysis_sequence, trigger_json
                FROM pm_snapshots
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            try:
                trigger = json.loads(row["trigger_json"] or "{}")
            except json.JSONDecodeError:
                trigger = {}
            result.append(
                {
                    "id": row["id"],
                    "createdAt": row["generated_at"] or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(row["created_at"])),
                    "model": row["model"],
                    "projectHealth": row["project_health"],
                    "currentStep": {
                        "number": row["step_number"],
                        "name": row["step_name"],
                    } if row["step_number"] is not None or row["step_name"] else None,
                    "headline": row["headline"],
                    "analysisSequence": row["analysis_sequence"],
                    "trigger": trigger,
                }
            )
        return result

    def get_pm_snapshot(self, snapshot_id: int) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM pm_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return json.loads(row["state_json"])
        except json.JSONDecodeError:
            return None


activity_store = ActivityStore()
