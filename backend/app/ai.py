from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx


def _clip(value: Any, limit: int = 4000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "…"


class AIStateStore:
    def __init__(self) -> None:
        root = Path(os.getenv("DEV_FLOW_AI_STATE_DIR", ".state/ai"))
        root.mkdir(parents=True, exist_ok=True)
        self.root = root

    def _path(self, repo: str) -> Path:
        return self.root / (repo.replace("/", "__") + ".json")

    def load(self, repo: str) -> dict[str, Any] | None:
        path = self._path(repo)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None

    def save(self, repo: str, payload: dict[str, Any]) -> None:
        path = self._path(repo)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        temp.replace(path)


class VertexAIAdvisor:
    def __init__(self) -> None:
        self.enabled = os.getenv("DEV_FLOW_AI_ENABLED", "false").lower() == "true"
        self.project = os.getenv("DEV_FLOW_AI_PROJECT", "flai-oosuhada-20260506")
        self.location = os.getenv("DEV_FLOW_AI_LOCATION", "global")
        self.reasoning_model = os.getenv(
            "DEV_FLOW_AI_REASONING_MODEL",
            os.getenv("DEV_FLOW_AI_MODEL", "gemini-3.7-flash"),
        )
        self.simple_model = os.getenv("DEV_FLOW_AI_SIMPLE_MODEL", "gemini-3.5-flash-lite")
        # Compatibility alias used by existing API payloads/tests.
        self.model = self.reasoning_model
        self.api_key = os.getenv("DEV_FLOW_AI_API_KEY", "")
        configured_trigger_mode = os.getenv("DEV_FLOW_AI_TRIGGER_MODE", "meaningful-events")
        # The old production value described the pre-guardrail implementation.
        # Keep health output truthful even before the server environment is
        # rewritten during deployment.
        self.trigger_mode = (
            "meaningful-events"
            if configured_trigger_mode == "webhook-every-event"
            else configured_trigger_mode
        )
        self.store = AIStateStore()
        self._locks: dict[str, asyncio.Lock] = {}
        self._commit_cache: dict[str, dict[str, Any]] = {}
        self.project_state_key = "__project__"
        self.project_memory_key = "__project_memory__"
        self.auto_budget_key = "__auto_pm_budget__"
        self.auto_daily_call_limit = self._int_env("DEV_FLOW_AI_DAILY_CALL_LIMIT", 30, minimum=1)
        self.auto_daily_input_token_limit = self._int_env(
            "DEV_FLOW_AI_DAILY_INPUT_TOKEN_LIMIT", 1_250_000, minimum=10_000
        )
        self.budget_timezone = os.getenv("DEV_FLOW_AI_BUDGET_TIMEZONE", "Asia/Seoul")

    @staticmethod
    def _int_env(name: str, default: int, *, minimum: int) -> int:
        try:
            return max(minimum, int(os.getenv(name, str(default))))
        except ValueError:
            return default

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def state(self, repo: str) -> dict[str, Any] | None:
        return self.store.load(repo)

    def project_state(self) -> dict[str, Any] | None:
        return self.store.load(self.project_state_key)

    def project_memory(self) -> dict[str, Any] | None:
        return self.store.load(self.project_memory_key)

    def _url(self, model: str | None = None) -> str:
        selected = model or self.reasoning_model
        return (
            f"https://aiplatform.googleapis.com/v1/projects/{self.project}/locations/{self.location}"
            f"/publishers/google/models/{selected}:generateContent?key={self.api_key}"
        )

    async def _generate_json(
        self,
        system: str,
        prompt: str,
        max_output_tokens: int = 4096,
        *,
        model: str | None = None,
        thinking_level: str | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.available:
            raise RuntimeError("AI advisor is not configured")
        selected_model = model or self.reasoning_model
        generation_config: dict[str, Any] = {
            "temperature": 0.2,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
        }
        if thinking_level:
            generation_config["thinkingConfig"] = {"thinkingLevel": thinking_level}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(self._url(selected_model), json=body)
        if response.status_code >= 400:
            raise RuntimeError(f"Vertex AI {response.status_code}: {response.text[:300]}")
        raw = response.json()
        parts = (((raw.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = "".join(str(part.get("text") or "") for part in parts)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Vertex AI returned non-JSON output: {text[:220]}") from exc
        usage = dict(raw.get("usageMetadata") or {})
        usage["model"] = selected_model
        return parsed, usage

    def _budget_day(self) -> str:
        try:
            zone = ZoneInfo(self.budget_timezone)
        except Exception:
            zone = ZoneInfo("UTC")
        return datetime.now(zone).date().isoformat()

    def auto_budget_status(self) -> dict[str, Any]:
        day = self._budget_day()
        stored = self.store.load(self.auto_budget_key) or {}
        if stored.get("day") != day:
            stored = {"day": day, "calls": 0, "inputTokens": 0}
        calls = int(stored.get("calls") or 0)
        input_tokens = int(stored.get("inputTokens") or 0)
        allowed = (
            calls < self.auto_daily_call_limit
            and input_tokens < self.auto_daily_input_token_limit
        )
        return {
            "day": day,
            "timezone": self.budget_timezone,
            "calls": calls,
            "callLimit": self.auto_daily_call_limit,
            "inputTokens": input_tokens,
            "inputTokenLimit": self.auto_daily_input_token_limit,
            "automaticAllowed": allowed,
        }

    def auto_pm_allowed(self) -> bool:
        return bool(self.auto_budget_status()["automaticAllowed"])

    def record_auto_pm_usage(self, usage: dict[str, Any] | None) -> dict[str, Any]:
        status = self.auto_budget_status()
        prompt_tokens = int((usage or {}).get("promptTokenCount") or 0)
        payload = {
            "day": status["day"],
            "calls": int(status["calls"]) + 1,
            "inputTokens": int(status["inputTokens"]) + prompt_tokens,
        }
        self.store.save(self.auto_budget_key, payload)
        return self.auto_budget_status()

    def project_semantic_revision(
        self,
        snapshots: dict[str, dict[str, Any]],
        project_memory: dict[str, Any],
    ) -> str:
        """Hash only state that should justify a fresh PM interpretation.

        Volatile timestamps are intentionally excluded so startup/fallback
        refreshes do not spend Vertex tokens when the actionable GitHub state
        is unchanged.
        """

        compact: dict[str, Any] = {}
        for repo, snapshot in sorted(snapshots.items()):
            repo_state = self._compact_snapshot(snapshot)
            for pull in repo_state.get("openPullRequests", []):
                pull.pop("updatedAt", None)
            for pull in repo_state.get("recentPullHistory", []):
                pull.pop("updatedAt", None)
            compact[repo] = repo_state
        raw = json.dumps(
            {
                "memoryRevision": project_memory.get("revision"),
                "repositories": compact,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def project_model_for(self, trigger: dict[str, Any]) -> tuple[str, str | None]:
        event = str(trigger.get("event") or "")
        context = trigger.get("eventContext") if isinstance(trigger.get("eventContext"), dict) else {}
        review_state = str((context or {}).get("reviewState") or "").lower()
        if event in {"pull_request_review", "issue_comment", "pull_request_review_comment"}:
            return self.reasoning_model, "MEDIUM"
        if review_state in {"approved", "changes_requested"}:
            return self.reasoning_model, "MEDIUM"
        return self.simple_model, None

    def _compact_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        pulls = []
        for pull in snapshot.get("pulls", []):
            if pull.get("lifecycle") != "open":
                continue
            human_reviews = [
                {"user": review.get("user"), "state": review.get("state"), "body": _clip(review.get("body"), 1000)}
                for review in pull.get("reviews", [])
                if not review.get("isBot")
            ]
            bot_reviews = [
                {"user": review.get("user"), "state": review.get("state"), "body": _clip(review.get("body"), 1800)}
                for review in pull.get("reviews", [])
                if review.get("isBot")
            ][-2:]
            pulls.append({
                "number": pull.get("number"),
                "title": pull.get("title"),
                "author": pull.get("author"),
                "head": pull.get("head"),
                "base": pull.get("base"),
                "draft": pull.get("draft"),
                "mergeable": pull.get("mergeable"),
                "mergeableState": pull.get("mergeableState"),
                "updatedAt": pull.get("updatedAt"),
                "requestedReviewers": pull.get("requestedReviewers"),
                "labels": pull.get("labels"),
                "commitCount": pull.get("commitCount"),
                "checks": pull.get("checks"),
                "humanReviews": human_reviews,
                "botReviews": bot_reviews,
            })
        return {
            "repository": snapshot.get("repository"),
            "defaultBranch": snapshot.get("defaultBranch"),
            "headSha": snapshot.get("headSha"),
            "openPullRequests": pulls,
            "relations": [edge for edge in snapshot.get("pullRelations", []) if edge.get("source") and edge.get("target")],
            "recentCommits": [
                {
                    "sha": commit.get("sha", "")[:12],
                    "message": commit.get("message"),
                    "author": commit.get("author"),
                    "timestamp": commit.get("timestamp"),
                    "prNumber": commit.get("prNumber"),
                }
                for commit in snapshot.get("commits", [])[:15]
            ],
            "recentPullHistory": [
                {
                    "number": pull.get("number"),
                    "title": pull.get("title"),
                    "author": pull.get("author"),
                    "lifecycle": pull.get("lifecycle"),
                    "createdAt": pull.get("createdAt"),
                    "updatedAt": pull.get("updatedAt"),
                    "mergedAt": pull.get("mergedAt"),
                    "closedAt": pull.get("closedAt"),
                    "head": pull.get("head"),
                    "base": pull.get("base"),
                }
                for pull in snapshot.get("pulls", [])[:30]
            ],
        }

    async def ensure_project_memory(self, documents: dict[str, str]) -> dict[str, Any]:
        """Build a persistent project charter from canonical docs only when they change."""
        digest = hashlib.sha256()
        for path in sorted(documents):
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(documents[path].encode("utf-8"))
            digest.update(b"\0")
        revision = digest.hexdigest()
        current = self.project_memory()
        if current and current.get("revision") == revision:
            return current

        source = "\n\n".join(
            f"===== {path} =====\n{_clip(text, 90_000)}"
            for path, text in documents.items()
        )
        system = """
You are extracting the durable project charter for an AI Project Manager.
Use ONLY the supplied canonical repository documentation. Do not add assumptions.
Preserve explicit Korean names and GitHub handles exactly when present.
Distinguish current/canonical requirements from historical provenance, Target ideas, and optional extensions.
The memory must help prevent overengineering and endless documentation loops, not encourage them.
Return JSON only with this shape:
{
  "projectName":"...",
  "northStar":"one sentence",
  "mvpGoal":"...",
  "canonicalFlow":["..."],
  "roles":[
    {"name":"성민","github":"smmini","role":"...","mission":"...","owns":["..."],"mustNot":["..."],"handoffs":["..."]}
  ],
  "deliveryRules":["..."],
  "antiPatterns":["..."],
  "outOfScope":["..."],
  "steps":[{"number":1,"name":"...","goal":"...","doneWhen":["..."]}],
  "completionDefinition":["..."],
  "sourceHierarchy":["which docs are canonical and which are history"]
}
Include all four team members and all explicitly defined execution Steps when present.
Important anti-patterns to retain when supported by docs include unnecessary framework expansion, documentation becoming a delivery gate, ownership boundary violations, and adding features not needed by the E2E MVP.
Write concise Korean values while preserving technical identifiers.
""".strip()
        result, usage = await self._generate_json(
            system,
            source,
            max_output_tokens=5000,
            model=self.simple_model,
        )
        payload = {
            **result,
            "revision": revision,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": self.simple_model,
            "sourceDocuments": list(documents.keys()),
            "usage": usage,
        }
        self.store.save(self.project_memory_key, payload)
        return payload

    async def analyze_project(
        self,
        snapshots: dict[str, dict[str, Any]],
        trigger: dict[str, Any],
        project_memory: dict[str, Any],
        changed_pull_detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous = self.project_state() or {}
        semantic_revision = self.project_semantic_revision(snapshots, project_memory)
        unresolved_human_changes: dict[tuple[str, int], dict[str, Any]] = {}
        for repo, snapshot in snapshots.items():
            for pull in snapshot.get("pulls", []):
                if pull.get("lifecycle") != "open" or not pull.get("number"):
                    continue
                latest_decisive: dict[str, str] = {}
                for review in pull.get("reviews", []):
                    if review.get("isBot"):
                        continue
                    user = str(review.get("user") or "").strip()
                    state = str(review.get("state") or "").upper()
                    if user and state in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                        latest_decisive[user] = state
                blockers = [user for user, state in latest_decisive.items() if state == "CHANGES_REQUESTED"]
                if blockers:
                    key = (repo, int(pull["number"]))
                    unresolved_human_changes[key] = {
                        "repository": repo,
                        "number": key[1],
                        "author": pull.get("author"),
                        "reviewers": blockers,
                        "title": pull.get("title"),
                    }
        compact_repositories = {
            repo: self._compact_snapshot(snapshot)
            for repo, snapshot in snapshots.items()
        }
        prompt = json.dumps(
            {
                "projectCharter": project_memory,
                "previousPMContext": _clip(previous.get("contextSummary"), 9000),
                "previousTeamActions": previous.get("teamActions", []),
                "previousHealth": previous.get("projectHealth"),
                "recentTriggers": previous.get("recentTriggers", [])[-25:],
                "trigger": trigger,
                "repositories": compact_repositories,
                "changedPullRequestDetail": self._compact_pull_detail(changed_pull_detail),
                "unresolvedHumanChangesRequested": list(unresolved_human_changes.values()),
            },
            ensure_ascii=False,
        )
        system = """
You are the persistent AI Project Manager for a four-person software project. The team lacks a human PM, so your job is to keep delivery moving toward the documented MVP instead of letting activity drift into planning-only work or overengineering.

SOURCE OF TRUTH:
- projectCharter was extracted from canonical project docs and defines goals, role ownership, execution steps, anti-patterns, out-of-scope work, and completion criteria.
- Git/GitHub repository facts are authoritative for current work. Never invent PRs, authors, approvals, checks, merges, commits, or dependencies.
- LLM interpretation is advisory. Do not rewrite factual ownership or architecture contracts.

PM BEHAVIOR:
1. Infer the CURRENT EXECUTION STEP from projectCharter.steps and actual implementation/PR evidence. Do not assume the team should remain in a documentation step simply because docs are being changed.
2. Give EACH of the four named team members exactly one concrete "NOW" action that can advance delivery. Prefer implementation, integration, review, validation, merge, or deployment over creating another planning document when the contract is already sufficient.
3. Detect delivery anti-patterns: docs churn without implementation, framework/platform expansion not required by E2E, ownership boundary violations, WIP/PR pile-up, review starvation, repeated planning, work that is already superseded, and optional scope distracting from the current gate.
4. If docs are already sufficient, explicitly say "새 문서 작성보다 구현/검증으로 이동" or equivalent when appropriate.
5. Respect dependency chains: upstream blockers usually come first. But assign independent work in parallel so four people are productive at the same time.
6. When a PR/comment/review/push changes the situation, revise prior assignments rather than restarting from scratch. Mention what changed.
7. The ultimate completion criterion is a working public E2E product where each owner can explain their part, not the number of documents or PRs.
8. A human CHANGES_REQUESTED review is a hard merge blocker until that reviewer's decisive state changes to APPROVED or DISMISSED. Never tell a reviewer/maintainer to merge such a PR first. Assign the PR author to address the requested changes, then request re-review, then merge only after the blocker is cleared. Automated reviews and green CI do not override this human blocker. Exception: if the correct project action is to CLOSE/abandon a superseded PR instead of merging it, closing the PR is allowed and you should not ask the author to fix code that will be discarded.
9. trigger.eventContext is fresh webhook evidence. Treat a just-submitted human review/comment body there as authoritative even if changedPullRequestDetail is missing because the GitHub REST API is rate-limited or stale.

Return JSON only:
{
  "headline":"what the team should do now",
  "projectHealth":"ON_TRACK|BLOCKED|DRIFT|OVERENGINEERING",
  "healthReason":"...",
  "currentStep":{"number":1,"name":"...","confidence":0.0,"why":"...","exitGate":"..."},
  "currentObjective":"single near-term delivery objective",
  "antiPatternAlerts":[{"severity":"info|warning|critical","title":"...","reason":"...","stopDoing":"...","doInstead":"..."}],
  "teamActions":[
    {"name":"...","github":"...","role":"...","now":"specific action","whyNow":"...","nextHandoff":"...","blockedBy":["..."],"relatedWork":[{"repository":"owner/repo","pr":123}],"confidence":0.0}
  ],
  "prPriorities":[
    {"repository":"owner/repo","number":123,"rank":1,"priority":"P0|P1|P2|P3","reason":"...","nextAction":"...","actorGithub":"...","impact":"...","confidence":0.0}
  ],
  "changesSinceLast":["..."],
  "contextSummary":"compact persistent PM memory for the next event"
}

Use Korean for user-facing text. Include exactly the four project members from projectCharter.roles in teamActions. Only use real open PRs in prPriorities/relatedWork. It is acceptable for a team member's NOW action to be work without a PR if that is the correct next integration task.
""".strip()
        selected_model, thinking_level = self.project_model_for(trigger)
        result, usage = await self._generate_json(
            system,
            prompt,
            max_output_tokens=6000 if selected_model == self.reasoning_model else 4000,
            model=selected_model,
            thinking_level=thinking_level,
        )

        valid_open = {
            (repo, int(pull["number"]))
            for repo, snapshot in snapshots.items()
            for pull in snapshot.get("pulls", [])
            if pull.get("lifecycle") == "open" and pull.get("number")
        }
        priorities: list[dict[str, Any]] = []
        for item in result.get("prPriorities", []):
            try:
                key = (str(item.get("repository")), int(item.get("number")))
            except (TypeError, ValueError):
                continue
            if key not in valid_open:
                continue
            normalized = {**item, "repository": key[0], "number": key[1]}
            human_blocker = unresolved_human_changes.get(key)
            intended_action = " ".join(
                str(normalized.get(field) or "")
                for field in ("nextAction", "reason", "impact")
            ).lower()
            closing_intent = any(token in intended_action for token in ("close", "폐기", "닫기", "종료", "discard", "abandon"))
            if human_blocker and not closing_intent:
                author = str(human_blocker.get("author") or normalized.get("actorGithub") or "author")
                reviewers = ", ".join(f"@{reviewer}" for reviewer in human_blocker.get("reviewers", []))
                normalized["actorGithub"] = author
                normalized["nextAction"] = (
                    f"@{author}: unresolved human CHANGES_REQUESTED를 반영해 수정 commit을 push하고 "
                    f"{reviewers or 'reviewer'}에게 재검증을 요청; 승인 전 merge 금지"
                )
                normalized["reason"] = (
                    f"사람 리뷰어 {reviewers or 'reviewer'}의 CHANGES_REQUESTED가 아직 unresolved입니다. "
                    "CI/자동리뷰가 green이어도 author 수정과 재승인이 먼저입니다."
                )
                if str(normalized.get("priority") or "") not in {"P0", "P1"}:
                    normalized["priority"] = "P1"
            priorities.append(normalized)
        result["prPriorities"] = priorities

        charter_roles = {
            str(role.get("github")): role
            for role in project_memory.get("roles", [])
            if role.get("github")
        }
        actions_by_github = {
            str(action.get("github")): action
            for action in result.get("teamActions", [])
            if action.get("github") in charter_roles
        }
        result["teamActions"] = [
            actions_by_github[github]
            for github in charter_roles
            if github in actions_by_github
        ]

        # Deterministic safety rail: a later unrelated webhook must not dilute a
        # still-unresolved human Request Changes into "merge now" advice.
        priority_rank = {
            (str(item.get("repository")), int(item.get("number"))): int(item.get("rank") or 999)
            for item in priorities
            if item.get("repository") and item.get("number")
        }
        blockers_by_author: dict[str, list[dict[str, Any]]] = {}
        for key, blocker in unresolved_human_changes.items():
            matching_priority = next(
                (item for item in priorities if str(item.get("repository")) == key[0] and int(item.get("number") or 0) == key[1]),
                None,
            )
            if matching_priority is not None:
                intended_action = " ".join(
                    str(matching_priority.get(field) or "")
                    for field in ("nextAction", "reason", "impact")
                ).lower()
                if any(token in intended_action for token in ("close", "폐기", "닫기", "종료", "discard", "abandon")):
                    continue
            author = str(blocker.get("author") or "").strip()
            if author and author in charter_roles:
                blockers_by_author.setdefault(author, []).append({**blocker, "rank": priority_rank.get(key, 999)})
        for author, blockers in blockers_by_author.items():
            blockers.sort(key=lambda item: (int(item.get("rank") or 999), int(item.get("number") or 0)))
            blocker = blockers[0]
            number = int(blocker["number"])
            repository = str(blocker["repository"])
            reviewers = ", ".join(f"@{reviewer}" for reviewer in blocker.get("reviewers", [])) or "reviewer"
            action = next((item for item in result["teamActions"] if item.get("github") == author), None)
            if action is not None:
                action["now"] = (
                    f"{repository.split('/')[-1]} PR #{number}의 unresolved human CHANGES_REQUESTED를 반영해 수정하고 "
                    f"{reviewers} 재검증을 받은 뒤 merge 단계로 이동"
                )
                action["whyNow"] = (
                    f"PR #{number}은 사람 리뷰의 Request Changes가 아직 유효합니다. "
                    "green CI나 자동 리뷰보다 이 blocker 해소가 먼저입니다."
                )
                related = list(action.get("relatedWork") or [])
                if not any(str(item.get("repository")) == repository and int(item.get("pr") or 0) == number for item in related):
                    related.insert(0, {"repository": repository, "pr": number})
                action["relatedWork"] = related

        recent_triggers = [*previous.get("recentTriggers", [])[-24:], trigger]
        payload = {
            **result,
            "status": "ready",
            "model": selected_model,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "trigger": trigger,
            "analysisSequence": int(previous.get("analysisSequence") or 0) + 1,
            "recentTriggers": recent_triggers,
            "projectMemoryRevision": project_memory.get("revision"),
            "semanticRevision": semantic_revision,
            "usage": usage,
        }
        self.store.save(self.project_state_key, payload)
        return payload

    async def chat_project(
        self,
        question: str,
        history: list[dict[str, str]],
        project_memory: dict[str, Any],
        project_state: dict[str, Any] | None,
    ) -> dict[str, Any]:
        prompt = json.dumps(
            {
                "projectCharter": project_memory,
                "currentPMState": project_state or {},
                "conversation": history[-12:],
                "question": question,
            },
            ensure_ascii=False,
        )
        system = """
You are the conversational interface of the same persistent AI Project Manager used by the dashboard.
Answer questions about what the team should do next using the canonical project charter and current PM state.
Do not invent GitHub facts. When information is not present, say that it is not confirmed.
Prefer concrete execution guidance over creating more planning documents when the contract is already sufficient.
Respect the four owners and their documented responsibility boundaries.
If the user asks "다음에 뭐할까?" or equivalent, give the single highest-value next action first, then the parallel actions for the other team members if useful.
Return JSON only: {"answer":"Korean markdown text","suggestedQuestions":["...","...","..."]}
""".strip()
        result, usage = await self._generate_json(
            system,
            prompt,
            max_output_tokens=3500,
            model=self.reasoning_model,
            thinking_level="MEDIUM",
        )
        return {
            "answer": str(result.get("answer") or ""),
            "suggestedQuestions": [str(item) for item in result.get("suggestedQuestions", [])[:4]],
            "model": self.reasoning_model,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "usage": usage,
        }

    def _compact_pull_detail(self, detail: dict[str, Any] | None) -> dict[str, Any] | None:
        if not detail:
            return None
        return {
            "number": detail.get("number"),
            "title": detail.get("title"),
            "author": detail.get("author"),
            "body": _clip(detail.get("body"), 7000),
            "stats": detail.get("stats"),
            "latestComments": [
                {"user": item.get("user"), "body": _clip(item.get("body"), 2400), "createdAt": item.get("createdAt")}
                for item in detail.get("comments", [])[-8:]
            ],
            "latestReviewComments": [
                {
                    "user": item.get("user"),
                    "path": item.get("path"),
                    "body": _clip(item.get("body"), 2000),
                    "createdAt": item.get("createdAt"),
                }
                for item in detail.get("reviewComments", [])[-8:]
            ],
            "latestReviews": [
                {"user": item.get("user"), "state": item.get("state"), "body": _clip(item.get("body"), 2600), "submittedAt": item.get("submittedAt")}
                for item in detail.get("reviews", [])[-8:]
            ],
            "latestEvents": detail.get("events", [])[-12:],
            "commits": detail.get("commits", [])[-8:],
            "checks": detail.get("checks", []),
        }

    async def analyze_repository(
        self,
        repo: str,
        snapshot: dict[str, Any],
        trigger: dict[str, Any],
        pull_detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        lock = self._locks.setdefault(repo, asyncio.Lock())
        async with lock:
            previous = self.store.load(repo) or {}
            previous_context = previous.get("contextSummary") or previous.get("summary") or "No previous AI analysis."
            compact = self._compact_snapshot(snapshot)
            open_numbers = {int(pull["number"]) for pull in compact["openPullRequests"] if pull.get("number")}
            prompt = json.dumps(
                {
                    "previousContext": _clip(previous_context, 7000),
                    "previousPriorityQueue": previous.get("priorities", [])[:10],
                    "recentTriggers": previous.get("recentTriggers", [])[-20:],
                    "trigger": trigger,
                    "repositoryState": compact,
                    "changedPullRequestDetail": self._compact_pull_detail(pull_detail),
                },
                ensure_ascii=False,
            )
            system = """
You are the persistent AI operations lead for a software team's GitHub development-flow dashboard.
The deterministic Git/GitHub data in the input is the source of truth. Never invent PRs, approvals, checks, dependencies, or merge state.
Your job is to continuously update the team's action priority after every GitHub event, preserving useful conclusions from previousContext while revising stale ones.
Optimize for reducing work-in-progress, unblocking dependency chains, closing/merging ready PRs, resolving requested changes, and avoiding reviewer contention.
Prefer actions that unblock multiple downstream PRs. Distinguish author work from reviewer work. Treat automated review findings as evidence, not human approval.
If a PR is blocked by another PR, normally prioritize the upstream blocker first. If a newer event changes the situation, explicitly explain the priority change.
Return JSON only with this exact high-level shape:
{
  "headline": "one sentence describing what the team should do now",
  "summary": "short current flow diagnosis",
  "changesSinceLast": ["what changed in this analysis"],
  "priorities": [
    {"rank":1,"number":123,"priority":"P0|P1|P2|P3","score":0,"reason":"why now","nextAction":"concrete next action","actor":"author|reviewer|maintainer|team","impact":"what becomes unblocked","confidence":0.0}
  ],
  "watch": [{"number":123,"signal":"short risk or event to watch"}],
  "contextSummary": "compact persistent context for the next model invocation"
}
Use only open PR numbers in priorities/watch. Rank every open PR when feasible. score is 0-100 and confidence 0-1.
Write user-facing text in Korean, but keep code identifiers and PR numbers unchanged.
""".strip()
            result, usage = await self._generate_json(
                system,
                prompt,
                max_output_tokens=4000,
                model=self.simple_model,
            )
            priorities = []
            seen: set[int] = set()
            for item in result.get("priorities", []):
                try:
                    number = int(item.get("number"))
                except (TypeError, ValueError):
                    continue
                if number not in open_numbers or number in seen:
                    continue
                seen.add(number)
                priorities.append({**item, "number": number})
            result["priorities"] = priorities
            result["watch"] = [
                item for item in result.get("watch", [])
                if isinstance(item, dict) and str(item.get("number", "")).isdigit() and int(item["number"]) in open_numbers
            ]
            payload = {
                **result,
                "repository": repo,
                "model": self.simple_model,
                "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "trigger": trigger,
                "analysisSequence": int(previous.get("analysisSequence") or 0) + 1,
                "recentTriggers": [*previous.get("recentTriggers", [])[-19:], trigger],
                "usage": usage,
                "status": "ready",
            }
            self.store.save(repo, payload)
            return payload

    async def analyze_commit(
        self,
        repo: str,
        commit: dict[str, Any],
        snapshot: dict[str, Any],
    ) -> dict[str, Any]:
        previous = self.project_state() or self.store.load(repo) or {}
        context_version = previous.get("generatedAt") or "initial"
        cache_key = f"{repo}:{commit.get('sha')}:{context_version}"
        if cache_key in self._commit_cache:
            return self._commit_cache[cache_key]
        files = [
            {
                "filename": item.get("filename"),
                "status": item.get("status"),
                "additions": item.get("additions"),
                "deletions": item.get("deletions"),
                "patch": _clip(item.get("patch"), 3500),
            }
            for item in commit.get("files", [])[:20]
        ]
        prompt = json.dumps(
            {
                "repositoryContext": _clip(previous.get("contextSummary") or previous.get("summary"), 6000),
                "openPullRequests": self._compact_snapshot(snapshot)["openPullRequests"],
                "commit": {
                    "sha": commit.get("sha"),
                    "message": commit.get("message"),
                    "author": commit.get("author"),
                    "stats": commit.get("stats"),
                    "parents": commit.get("parents"),
                    "files": files,
                },
            },
            ensure_ascii=False,
        )
        system = """
You are an AI code-flow analyst embedded in a Git Graph style commit inspector.
Git/GitHub facts in the input are authoritative. Analyze the selected immutable commit in the context of the repository's currently open PR flow.
Do not perform a generic code review. Explain operational impact: what changed, risky areas, what a reviewer should inspect, whether it affects current PR bottlenecks, and the next useful action.
Return JSON only:
{"summary":"...","riskLevel":"LOW|MEDIUM|HIGH|CRITICAL","whyItMatters":"...","affectedAreas":["..."],"reviewFocus":["..."],"relatedPulls":[123],"recommendedNextStep":"...","confidence":0.0}
Write user-facing text in Korean.
""".strip()
        result, usage = await self._generate_json(
            system,
            prompt,
            max_output_tokens=3000,
            model=self.reasoning_model,
            thinking_level="MEDIUM",
        )
        open_numbers = {int(p["number"]) for p in snapshot.get("pulls", []) if p.get("lifecycle") == "open" and p.get("number")}
        result["relatedPulls"] = [n for n in result.get("relatedPulls", []) if isinstance(n, int) and n in open_numbers]
        payload = {
            **result,
            "repository": repo,
            "sha": commit.get("sha"),
            "model": self.reasoning_model,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "usage": usage,
            "status": "ready",
        }
        self._commit_cache[cache_key] = payload
        return payload


ai_advisor = VertexAIAdvisor()
