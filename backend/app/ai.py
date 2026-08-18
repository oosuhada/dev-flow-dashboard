from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

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
        self.model = os.getenv("DEV_FLOW_AI_MODEL", "gemini-3.7-flash")
        self.api_key = os.getenv("DEV_FLOW_AI_API_KEY", "")
        self.trigger_mode = os.getenv("DEV_FLOW_AI_TRIGGER_MODE", "webhook-every-event")
        self.store = AIStateStore()
        self._locks: dict[str, asyncio.Lock] = {}
        self._commit_cache: dict[str, dict[str, Any]] = {}

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def state(self, repo: str) -> dict[str, Any] | None:
        return self.store.load(repo)

    def _url(self) -> str:
        return (
            f"https://aiplatform.googleapis.com/v1/projects/{self.project}/locations/{self.location}"
            f"/publishers/google/models/{self.model}:generateContent?key={self.api_key}"
        )

    async def _generate_json(self, system: str, prompt: str, max_output_tokens: int = 4096) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.available:
            raise RuntimeError("AI advisor is not configured")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            response = await client.post(self._url(), json=body)
        if response.status_code >= 400:
            raise RuntimeError(f"Vertex AI {response.status_code}: {response.text[:300]}")
        raw = response.json()
        parts = (((raw.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
        text = "".join(str(part.get("text") or "") for part in parts)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Vertex AI returned non-JSON output: {text[:220]}") from exc
        return parsed, raw.get("usageMetadata") or {}

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
            result, usage = await self._generate_json(system, prompt, max_output_tokens=5000)
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
                "model": self.model,
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
        previous = self.store.load(repo) or {}
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
        result, usage = await self._generate_json(system, prompt, max_output_tokens=3000)
        open_numbers = {int(p["number"]) for p in snapshot.get("pulls", []) if p.get("lifecycle") == "open" and p.get("number")}
        result["relatedPulls"] = [n for n in result.get("relatedPulls", []) if isinstance(n, int) and n in open_numbers]
        payload = {
            **result,
            "repository": repo,
            "sha": commit.get("sha"),
            "model": self.model,
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "usage": usage,
            "status": "ready",
        }
        self._commit_cache[cache_key] = payload
        return payload


ai_advisor = VertexAIAdvisor()
