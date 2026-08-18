from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"


def configured_repositories() -> list[str]:
    raw = os.getenv(
        "GITHUB_REPOSITORIES",
        "Biz-CollabCraft/ontology_dashboard,Biz-CollabCraft/gen_data",
    )
    repos = [item.strip() for item in raw.split(",") if item.strip()]
    return repos or ["Biz-CollabCraft/ontology_dashboard"]


def cache_ttl_seconds() -> int:
    try:
        return max(5, min(300, int(os.getenv("CACHE_TTL_SECONDS", "45"))))
    except ValueError:
        return 45


@dataclass
class CacheEntry:
    expires_at: float
    value: dict[str, Any]


class SnapshotCache:
    def __init__(self, ttl_seconds: int | None = None) -> None:
        self.ttl_seconds = ttl_seconds or cache_ttl_seconds()
        self._items: dict[str, CacheEntry] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        item = self._items.get(key)
        if item is None or item.expires_at <= time.monotonic():
            self._items.pop(key, None)
            return None
        return item.value

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._items[key] = CacheEntry(time.monotonic() + self.ttl_seconds, value)

    def clear(self, key: str) -> None:
        self._items.pop(key, None)


class GitHubAggregator:
    def __init__(self, token: str | None = None, cache: SnapshotCache | None = None) -> None:
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN")
        self.cache = cache or SnapshotCache()

    @property
    def authenticated(self) -> bool:
        return bool(self.token)

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "dev-flow-dashboard",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _get(self, client: httpx.AsyncClient, path: str) -> tuple[Any, int | None, int | None]:
        response = await client.get(f"{GITHUB_API}{path}", headers=self._headers())
        remaining = int(response.headers["x-ratelimit-remaining"]) if response.headers.get("x-ratelimit-remaining", "").isdigit() else None
        limit = int(response.headers["x-ratelimit-limit"]) if response.headers.get("x-ratelimit-limit", "").isdigit() else None
        if response.status_code >= 400:
            detail = response.text[:220]
            raise RuntimeError(f"GitHub API {response.status_code}: {detail}")
        return response.json(), remaining, limit

    async def snapshot(self, repo: str, force: bool = False) -> dict[str, Any]:
        if repo not in configured_repositories():
            raise ValueError("Repository is not configured")
        if not force:
            cached = self.cache.get(repo)
            if cached is not None:
                return {**cached, "cache": {"hit": True, "ttlSeconds": self.cache.ttl_seconds}}

        owner, name = repo.split("/", 1)
        root = f"/repos/{owner}/{name}"
        rate_values: list[int] = []
        rate_limits: list[int] = []

        async with httpx.AsyncClient(timeout=20.0) as client:
            pull_list, remaining, limit = await self._get(client, f"{root}/pulls?state=open&per_page=100&sort=updated&direction=desc")
            if remaining is not None:
                rate_values.append(remaining)
            if limit is not None:
                rate_limits.append(limit)

            async def enrich(summary: dict[str, Any]) -> dict[str, Any]:
                number = summary["number"]
                head_sha = summary["head"]["sha"]
                results = await asyncio.gather(
                    self._get(client, f"{root}/pulls/{number}"),
                    self._get(client, f"{root}/pulls/{number}/reviews?per_page=100"),
                    self._get(client, f"{root}/commits/{head_sha}/check-runs?per_page=100"),
                    self._get(client, f"{root}/pulls/{number}/commits?per_page=100"),
                )
                for _, rem, lim in results:
                    if rem is not None:
                        rate_values.append(rem)
                    if lim is not None:
                        rate_limits.append(lim)
                detail, reviews, checks, commits = (result[0] for result in results)
                return normalize_pull(detail, reviews, checks.get("check_runs", []), commits)

            pulls = await asyncio.gather(*(enrich(item) for item in pull_list))
            main_commits_raw, rem, lim = await self._get(client, f"{root}/commits?sha=main&per_page=40")
            if rem is not None:
                rate_values.append(rem)
            if lim is not None:
                rate_limits.append(lim)

        commits_by_sha: dict[str, dict[str, Any]] = {}
        for item in main_commits_raw:
            commits_by_sha[item["sha"]] = normalize_commit(item, "main")
        for pull in pulls:
            for item in pull.pop("_commits"):
                normalized = normalize_commit(item, pull["head"], pull["number"])
                commits_by_sha.setdefault(normalized["sha"], normalized)

        snapshot = {
            "repository": repo,
            "pulls": pulls,
            "commits": sorted(commits_by_sha.values(), key=lambda item: item["timestamp"], reverse=True)[:100],
            "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "rateLimit": {
                "remaining": min(rate_values) if rate_values else None,
                "limit": max(rate_limits) if rate_limits else None,
            },
            "authentication": "authenticated" if self.authenticated else "public",
            "cache": {"hit": False, "ttlSeconds": self.cache.ttl_seconds},
        }
        self.cache.set(repo, snapshot)
        return snapshot


def normalize_pull(detail: dict[str, Any], reviews: list[dict[str, Any]], checks: list[dict[str, Any]], commits: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "number": detail["number"],
        "title": detail.get("title", ""),
        "state": detail.get("state", "open"),
        "url": detail.get("html_url", ""),
        "author": (detail.get("user") or {}).get("login", "unknown"),
        "base": (detail.get("base") or {}).get("ref", "main"),
        "head": (detail.get("head") or {}).get("ref", "unknown"),
        "headSha": (detail.get("head") or {}).get("sha", ""),
        "draft": bool(detail.get("draft")),
        "mergeable": detail.get("mergeable"),
        "mergeableState": detail.get("mergeable_state"),
        "createdAt": detail.get("created_at"),
        "updatedAt": detail.get("updated_at"),
        "requestedReviewers": [item.get("login", "unknown") for item in detail.get("requested_reviewers", [])],
        "labels": [item.get("name", "") for item in detail.get("labels", [])],
        "commitCount": len(commits),
        "reviews": [
            {
                "user": (review.get("user") or {}).get("login", "unknown"),
                "state": review.get("state", "COMMENTED"),
                "body": review.get("body") or "",
                "submittedAt": review.get("submitted_at"),
                "isBot": (review.get("user") or {}).get("type") == "Bot" or str((review.get("user") or {}).get("login", "")).endswith("[bot]"),
            }
            for review in reviews
        ],
        "checks": [
            {
                "name": check.get("name", "check"),
                "status": check.get("status", "queued"),
                "conclusion": check.get("conclusion"),
                "url": check.get("html_url") or check.get("details_url"),
            }
            for check in checks
        ],
        "_commits": commits,
    }


def normalize_commit(commit: dict[str, Any], branch: str, pr_number: int | None = None) -> dict[str, Any]:
    commit_data = commit.get("commit") or {}
    author_data = commit_data.get("author") or {}
    return {
        "sha": commit.get("sha", ""),
        "message": str(commit_data.get("message") or "Commit").split("\n", 1)[0],
        "author": author_data.get("name") or (commit.get("author") or {}).get("login", "unknown"),
        "timestamp": author_data.get("date") or "1970-01-01T00:00:00Z",
        "parents": [parent.get("sha", "") for parent in commit.get("parents", []) if parent.get("sha")],
        "branch": branch,
        "prNumber": pr_number,
    }

