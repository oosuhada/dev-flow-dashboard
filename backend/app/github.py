from __future__ import annotations

import asyncio
import base64
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

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
            initial = await asyncio.gather(
                self._get(client, root),
                self._get(client, f"{root}/pulls?state=open&per_page=100&sort=updated&direction=desc"),
                self._get(client, f"{root}/branches?per_page=100"),
                self._get(client, f"{root}/tags?per_page=100"),
            )
            for _, remaining, limit in initial:
                if remaining is not None:
                    rate_values.append(remaining)
                if limit is not None:
                    rate_limits.append(limit)
            repo_meta, pull_list, branches_raw, tags_raw = (result[0] for result in initial)
            default_branch = str(repo_meta.get("default_branch") or "main")

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
            main_commits_raw, rem, lim = await self._get(client, f"{root}/commits?sha={quote(default_branch, safe='')}&per_page=100")
            if rem is not None:
                rate_values.append(rem)
            if lim is not None:
                rate_limits.append(lim)

        commits_by_sha: dict[str, dict[str, Any]] = {}
        for item in main_commits_raw:
            commits_by_sha[item["sha"]] = normalize_commit(item, default_branch)
        for pull in pulls:
            for item in pull.pop("_commits"):
                normalized = normalize_commit(item, pull["head"], pull["number"])
                commits_by_sha.setdefault(normalized["sha"], normalized)

        refs: list[dict[str, str]] = []
        for branch in branches_raw:
            sha = ((branch.get("commit") or {}).get("sha") or "").strip()
            name = str(branch.get("name") or "").strip()
            if sha and name:
                refs.append({"sha": sha, "name": name, "type": "head"})
        for tag in tags_raw:
            sha = ((tag.get("commit") or {}).get("sha") or "").strip()
            name = str(tag.get("name") or "").strip()
            if sha and name:
                refs.append({"sha": sha, "name": name, "type": "tag"})
        known_refs = {(ref["sha"], ref["name"], ref["type"]) for ref in refs}
        for pull in pulls:
            key = (pull["headSha"], pull["head"], "remote")
            if pull["headSha"] and pull["head"] and key not in known_refs:
                refs.append({"sha": pull["headSha"], "name": pull["head"], "type": "remote"})
                known_refs.add(key)
        refs_by_sha: dict[str, list[dict[str, str]]] = {}
        for ref in refs:
            refs_by_sha.setdefault(ref["sha"], []).append({"name": ref["name"], "type": ref["type"]})
        for sha, commit in commits_by_sha.items():
            commit["refs"] = refs_by_sha.get(sha, [])

        ordered_commits = topological_date_order(commits_by_sha)[:100]
        head_sha = next(
            (((branch.get("commit") or {}).get("sha") or "") for branch in branches_raw if branch.get("name") == default_branch),
            ordered_commits[0]["sha"] if ordered_commits else None,
        )

        snapshot = {
            "repository": repo,
            "defaultBranch": default_branch,
            "headSha": head_sha,
            "pulls": pulls,
            "commits": ordered_commits,
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

    async def commit_detail(self, repo: str, sha: str) -> dict[str, Any]:
        if repo not in configured_repositories():
            raise ValueError("Repository is not configured")
        owner, name = repo.split("/", 1)
        root = f"/repos/{owner}/{name}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            detail, _, _ = await self._get(client, f"{root}/commits/{quote(sha, safe='')}")
        commit_data = detail.get("commit") or {}
        author = commit_data.get("author") or {}
        committer = commit_data.get("committer") or {}
        return {
            "sha": detail.get("sha", sha),
            "htmlUrl": detail.get("html_url", ""),
            "message": commit_data.get("message") or "Commit",
            "author": author.get("name") or (detail.get("author") or {}).get("login", "unknown"),
            "authorEmail": author.get("email"),
            "authoredAt": author.get("date"),
            "committer": committer.get("name") or (detail.get("committer") or {}).get("login", "unknown"),
            "committedAt": committer.get("date"),
            "parents": [item.get("sha", "") for item in detail.get("parents", []) if item.get("sha")],
            "stats": detail.get("stats") or {"additions": 0, "deletions": 0, "total": 0},
            "files": [
                {
                    "filename": item.get("filename", ""),
                    "previousFilename": item.get("previous_filename"),
                    "status": item.get("status", "modified"),
                    "additions": item.get("additions", 0),
                    "deletions": item.get("deletions", 0),
                    "changes": item.get("changes", 0),
                    "patch": item.get("patch"),
                    "blobUrl": item.get("blob_url"),
                    "rawUrl": item.get("raw_url"),
                }
                for item in detail.get("files", [])
            ],
        }
    async def file_content(self, repo: str, sha: str, path: str) -> dict[str, Any]:
        if repo not in configured_repositories():
            raise ValueError("Repository is not configured")
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError("Invalid file path")
        owner, name = repo.split("/", 1)
        root = f"/repos/{owner}/{name}"
        encoded_path = quote(path, safe="/")
        async with httpx.AsyncClient(timeout=20.0) as client:
            payload, _, _ = await self._get(client, f"{root}/contents/{encoded_path}?ref={quote(sha, safe='')}")
        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise ValueError("Path is not a file")
        encoded = str(payload.get("content") or "").replace("\n", "")
        raw = base64.b64decode(encoded) if encoded else b""
        max_bytes = 750_000
        truncated = len(raw) > max_bytes
        raw = raw[:max_bytes]
        try:
            text = raw.decode("utf-8")
            binary = False
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            binary = True
        return {
            "path": path,
            "sha": payload.get("sha", sha),
            "size": payload.get("size", len(raw)),
            "content": text,
            "binary": binary,
            "truncated": truncated,
            "htmlUrl": payload.get("html_url", ""),
        }


def topological_date_order(commits_by_sha: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Order commits like git log: every child before its in-set parents, newest tips first."""
    child_counts = {sha: 0 for sha in commits_by_sha}
    for commit in commits_by_sha.values():
        for parent in commit.get("parents", []):
            if parent in child_counts:
                child_counts[parent] += 1

    ready = [sha for sha, count in child_counts.items() if count == 0]
    ordered: list[dict[str, Any]] = []
    emitted: set[str] = set()

    while ready:
        ready.sort(key=lambda sha: commits_by_sha[sha].get("timestamp", ""), reverse=True)
        sha = ready.pop(0)
        if sha in emitted:
            continue
        emitted.add(sha)
        commit = commits_by_sha[sha]
        ordered.append(commit)
        for parent in commit.get("parents", []):
            if parent not in child_counts:
                continue
            child_counts[parent] -= 1
            if child_counts[parent] == 0:
                ready.append(parent)

    if len(ordered) != len(commits_by_sha):
        leftovers = [commit for sha, commit in commits_by_sha.items() if sha not in emitted]
        leftovers.sort(key=lambda item: item.get("timestamp", ""), reverse=True)
        ordered.extend(leftovers)
    return ordered


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
        "email": author_data.get("email") or "",
        "timestamp": author_data.get("date") or "1970-01-01T00:00:00Z",
        "parents": [parent.get("sha", "") for parent in commit.get("parents", []) if parent.get("sha")],
        "branch": branch,
        "prNumber": pr_number,
        "refs": [],
    }

