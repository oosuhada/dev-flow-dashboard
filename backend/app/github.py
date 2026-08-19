from __future__ import annotations

import asyncio
import base64
import copy
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import jwt

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
        state_root = Path(os.getenv("DEV_FLOW_STATE_DIR", ".state")) / "snapshots"
        state_root.mkdir(parents=True, exist_ok=True)
        self.state_root = state_root

    def _path(self, key: str) -> Path:
        return self.state_root / (key.replace("/", "__") + ".json")

    def get(self, key: str) -> dict[str, Any] | None:
        item = self._items.get(key)
        if item is None or item.expires_at <= time.monotonic():
            self._items.pop(key, None)
            return None
        return item.value

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._items[key] = CacheEntry(time.monotonic() + self.ttl_seconds, value)
        path = self._path(key)
        temp = path.with_suffix(".tmp")
        try:
            temp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            temp.replace(path)
        except OSError:
            pass

    def clear(self, key: str) -> None:
        self._items.pop(key, None)

    def get_stale(self, key: str) -> dict[str, Any] | None:
        item = self._items.get(key)
        if item is not None:
            return item.value
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None


class GitHubRateLimitError(RuntimeError):
    """Raised without making a request while the shared REST circuit is open."""


class GitHubAppAuth:
    def __init__(self) -> None:
        self.app_id = os.getenv("GITHUB_APP_ID", "").strip()
        self.installation_id = os.getenv("GITHUB_APP_INSTALLATION_ID", "").strip()
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def requested(self) -> bool:
        return bool(self.app_id or self.installation_id or self._private_key_source())

    @property
    def configured(self) -> bool:
        return bool(self.app_id and self.installation_id and self._private_key_source())

    def _private_key_source(self) -> str:
        return (
            os.getenv("GITHUB_APP_PRIVATE_KEY", "").strip()
            or os.getenv("GITHUB_APP_PRIVATE_KEY_BASE64", "").strip()
            or os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()
        )

    def _private_key(self) -> str:
        inline = os.getenv("GITHUB_APP_PRIVATE_KEY", "").strip()
        if inline:
            return inline.replace("\\n", "\n")
        encoded = os.getenv("GITHUB_APP_PRIVATE_KEY_BASE64", "").strip()
        if encoded:
            return base64.b64decode(encoded).decode("utf-8")
        path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "").strip()
        if path:
            return Path(path).read_text()
        raise RuntimeError("GitHub App private key is not configured")

    async def token(self) -> str:
        now = time.time()
        if self._token and self._expires_at > now + 300:
            return self._token
        async with self._lock:
            now = time.time()
            if self._token and self._expires_at > now + 300:
                return self._token
            app_jwt = jwt.encode(
                {"iat": int(now) - 60, "exp": int(now) + 540, "iss": self.app_id},
                self._private_key(),
                algorithm="RS256",
            )
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {app_jwt}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "dev-flow-dashboard",
            }
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    f"{GITHUB_API}/app/installations/{self.installation_id}/access_tokens",
                    headers=headers,
                )
            if response.status_code >= 400:
                raise RuntimeError(f"GitHub App authentication failed ({response.status_code})")
            payload = response.json()
            token = str(payload.get("token") or "")
            if not token:
                raise RuntimeError("GitHub App authentication returned no installation token")
            self._token = token
            # Installation tokens normally last one hour. Parsing ISO timestamps
            # is unnecessary here; a conservative refresh keeps credentials fresh.
            self._expires_at = now + 3300
            return token


class GitHubAggregator:
    def __init__(self, token: str | None = None, cache: SnapshotCache | None = None) -> None:
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN")
        self.app_auth = GitHubAppAuth()
        self.cache = cache or SnapshotCache()
        self._conditional: dict[str, tuple[str, Any]] = {}
        self._rest_paused_until = 0.0
        self._pause_reason: str | None = None

    @property
    def authenticated(self) -> bool:
        return self.app_auth.configured or (not self.app_auth.requested and bool(self.token))

    @property
    def authentication_mode(self) -> str:
        if self.app_auth.configured:
            return "github-app"
        if self.app_auth.requested:
            return "github-app-misconfigured"
        return "token" if self.token else "public"

    def rate_limit_status(self) -> dict[str, Any]:
        remaining = max(0, int(self._rest_paused_until - time.time()))
        return {
            "paused": remaining > 0,
            "pausedUntil": self._rest_paused_until if remaining > 0 else None,
            "retryAfterSeconds": remaining,
            "reason": self._pause_reason if remaining > 0 else None,
        }

    def stale_snapshot(self, repo: str) -> dict[str, Any] | None:
        cached = self.cache.get_stale(repo)
        if cached is None:
            return None
        return {
            **cached,
            "cache": {"hit": True, "ttlSeconds": self.cache.ttl_seconds, "stale": True},
        }

    async def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "dev-flow-dashboard",
        }
        if self.app_auth.configured:
            headers["Authorization"] = f"Bearer {await self.app_auth.token()}"
        elif self.app_auth.requested:
            raise RuntimeError("GitHub App configuration is incomplete; refusing PAT fallback")
        elif self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _get(self, client: httpx.AsyncClient, path: str) -> tuple[Any, int | None, int | None]:
        now = time.time()
        if self._rest_paused_until > now:
            wait = max(1, int(self._rest_paused_until - now))
            raise GitHubRateLimitError(f"GitHub REST paused for {wait}s after rate limiting")
        headers = await self._headers()
        conditional = self._conditional.get(path)
        if conditional is not None:
            headers["If-None-Match"] = conditional[0]
        response = await client.get(f"{GITHUB_API}{path}", headers=headers)
        remaining = int(response.headers["x-ratelimit-remaining"]) if response.headers.get("x-ratelimit-remaining", "").isdigit() else None
        limit = int(response.headers["x-ratelimit-limit"]) if response.headers.get("x-ratelimit-limit", "").isdigit() else None
        if response.status_code == 304 and conditional is not None:
            return copy.deepcopy(conditional[1]), remaining, limit
        if response.status_code >= 400:
            body = response.text.lower()
            retry_after = response.headers.get("retry-after", "")
            reset = response.headers.get("x-ratelimit-reset", "")
            is_limited = response.status_code == 429 or (
                response.status_code == 403
                and (remaining == 0 or retry_after.isdigit() or "rate limit" in body)
            )
            if is_limited:
                candidates = [time.time() + 60]
                if retry_after.isdigit():
                    candidates.append(time.time() + int(retry_after))
                if reset.isdigit():
                    candidates.append(float(reset))
                self._rest_paused_until = max(self._rest_paused_until, *candidates)
                self._pause_reason = "primary" if remaining == 0 else "secondary"
            detail = response.text[:220]
            error_type = GitHubRateLimitError if is_limited else RuntimeError
            raise error_type(f"GitHub API {response.status_code}: {detail}")
        payload = response.json()
        etag = response.headers.get("etag")
        if etag:
            self._conditional[path] = (etag, copy.deepcopy(payload))
        return payload, remaining, limit

    def apply_webhook(self, repo: str, event_name: str, payload: dict[str, Any], number: int | None) -> bool:
        """Patch the last snapshot from fresh webhook evidence without REST."""
        snapshot = self.cache.get_stale(repo)
        if snapshot is None:
            return False
        updated = copy.deepcopy(snapshot)
        pulls = updated.get("pulls") if isinstance(updated.get("pulls"), list) else []
        pull_index = next((i for i, item in enumerate(pulls) if item.get("number") == number), None)
        raw_pull = payload.get("pull_request") if isinstance(payload.get("pull_request"), dict) else None

        if raw_pull is not None:
            current = pulls[pull_index] if pull_index is not None else None
            patched = normalize_pull(raw_pull, [], [], [])
            patched.pop("_commits", None)
            if current:
                patched["reviews"] = current.get("reviews", [])
                patched["checks"] = current.get("checks", [])
                patched["commitCount"] = current.get("commitCount", 0)
            if pull_index is None:
                pulls.insert(0, patched)
                pull_index = 0
            else:
                pulls[pull_index] = patched

        current = pulls[pull_index] if pull_index is not None else None
        if current is not None and event_name == "pull_request_review":
            review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
            normalized = {
                "user": (review.get("user") or {}).get("login", "unknown"),
                "state": str(review.get("state") or "COMMENTED").upper(),
                "body": review.get("body") or "",
                "submittedAt": review.get("submitted_at"),
                "isBot": (review.get("user") or {}).get("type") == "Bot",
            }
            reviews = list(current.get("reviews") or [])
            reviews.append(normalized)
            current["reviews"] = reviews
        elif current is not None and event_name == "check_run":
            check = payload.get("check_run") if isinstance(payload.get("check_run"), dict) else {}
            normalized = {
                "name": check.get("name", "check"),
                "status": check.get("status", "queued"),
                "conclusion": check.get("conclusion"),
                "url": check.get("html_url") or check.get("details_url"),
            }
            checks = [item for item in (current.get("checks") or []) if item.get("name") != normalized["name"]]
            current["checks"] = [normalized, *checks]
        elif current is not None and event_name in {"issue_comment", "pull_request_review_comment"}:
            if payload.get("action") == "created":
                current["commentsCount"] = int(current.get("commentsCount") or 0) + 1
        elif event_name == "push":
            ref = str(payload.get("ref") or "")
            branch = ref.removeprefix("refs/heads/")
            if branch == updated.get("defaultBranch"):
                updated["headSha"] = payload.get("after") or updated.get("headSha")
                commits = []
                for item in payload.get("commits") or []:
                    author = item.get("author") or {}
                    commits.append({
                        "sha": item.get("id", ""),
                        "message": str(item.get("message") or "Commit").split("\n", 1)[0],
                        "author": author.get("name", "unknown"),
                        "email": author.get("email", ""),
                        "timestamp": item.get("timestamp") or "1970-01-01T00:00:00Z",
                        "parents": [],
                        "branch": branch,
                        "refs": [],
                    })
                existing = [item for item in (updated.get("commits") or []) if item.get("sha") not in {c["sha"] for c in commits}]
                updated["commits"] = [*reversed(commits), *existing][:100]

        if raw_pull is None and current is None and event_name != "push":
            return False
        updated["pulls"] = pulls
        updated["pullRelations"] = derive_pull_relations(pulls)
        updated["fetchedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        updated["cache"] = {"hit": True, "ttlSeconds": self.cache.ttl_seconds, "source": "webhook"}
        self.cache.set(repo, updated)
        return True

    async def repository_fingerprint(self, repo: str) -> str:
        """Cheap change detector used by the live SSE watcher.

        Pull request `updated_at` changes for reviews/comments and a new head SHA
        changes for pushes. The latest default-branch commit catches merged/direct
        pushes that may not otherwise move the most-recent PR list.
        """
        if repo not in configured_repositories():
            raise ValueError("Repository is not configured")
        owner, name = repo.split("/", 1)
        root = f"/repos/{owner}/{name}"
        async with httpx.AsyncClient(timeout=12.0) as client:
            repo_meta, _, _ = await self._get(client, root)
            default_branch = str(repo_meta.get("default_branch") or "main")
            pulls, _, _ = await self._get(
                client,
                f"{root}/pulls?state=all&per_page=20&sort=updated&direction=desc",
            )
            commits, _, _ = await self._get(
                client,
                f"{root}/commits?sha={quote(default_branch, safe='')}&per_page=1",
            )
        pull_bits = [
            ":".join(
                [
                    str(item.get("number", "")),
                    str(item.get("updated_at", "")),
                    str(((item.get("head") or {}).get("sha") or "")),
                    str(item.get("state", "")),
                    str(bool(item.get("draft"))),
                ]
            )
            for item in pulls
        ]
        head_sha = str((commits[0] if commits else {}).get("sha", ""))
        return "|".join([head_sha, *pull_bits])

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
                self._get(client, f"{root}/pulls?state=all&per_page=100&sort=updated&direction=desc"),
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

            open_pull_list = [item for item in pull_list if item.get("state") == "open"]
            open_pulls = await asyncio.gather(*(enrich(item) for item in open_pull_list))
            enriched_by_number = {pull["number"]: pull for pull in open_pulls}
            pulls = [
                enriched_by_number.get(item["number"]) or normalize_pull(item, [], [], [])
                for item in pull_list
            ]
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

        pull_relations = derive_pull_relations(pulls)
        snapshot = {
            "repository": repo,
            "defaultBranch": default_branch,
            "headSha": head_sha,
            "pulls": pulls,
            "pullRelations": pull_relations,
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

    async def refresh_pull(self, repo: str, number: int) -> dict[str, Any] | None:
        """Refresh one PR and patch it into the local snapshot (four REST reads)."""
        if repo not in configured_repositories():
            raise ValueError("Repository is not configured")
        if number <= 0:
            raise ValueError("Invalid pull request number")
        base = self.cache.get_stale(repo)
        if base is None:
            return None
        owner, name = repo.split("/", 1)
        root = f"/repos/{owner}/{name}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            detail, _, _ = await self._get(client, f"{root}/pulls/{number}")
            head_sha = str(((detail.get("head") or {}).get("sha") or ""))
            reviews_result, checks_result, commits_result = await asyncio.gather(
                self._get(client, f"{root}/pulls/{number}/reviews?per_page=100"),
                self._get(client, f"{root}/commits/{head_sha}/check-runs?per_page=100"),
                self._get(client, f"{root}/pulls/{number}/commits?per_page=100"),
            )
        reviews = reviews_result[0]
        checks_payload = checks_result[0]
        commits_raw = commits_result[0]
        checks = checks_payload.get("check_runs", []) if isinstance(checks_payload, dict) else []
        normalized = normalize_pull(detail, reviews, checks, commits_raw)
        pr_commits = normalized.pop("_commits")

        updated = copy.deepcopy(base)
        pulls = [item for item in (updated.get("pulls") or []) if item.get("number") != number]
        pulls.append(normalized)
        pulls.sort(key=lambda item: str(item.get("updatedAt") or ""), reverse=True)
        updated["pulls"] = pulls
        updated["pullRelations"] = derive_pull_relations(pulls)

        commits = [item for item in (updated.get("commits") or []) if item.get("prNumber") != number]
        for item in pr_commits:
            candidate = normalize_commit(item, normalized["head"], number)
            if not any(current.get("sha") == candidate["sha"] for current in commits):
                candidate["refs"] = (
                    [{"name": normalized["head"], "type": "remote"}]
                    if candidate["sha"] == normalized["headSha"]
                    else []
                )
                commits.append(candidate)
        commits.sort(key=lambda item: str(item.get("timestamp") or ""), reverse=True)
        updated["commits"] = commits[:100]
        updated["fetchedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        updated["cache"] = {"hit": False, "ttlSeconds": self.cache.ttl_seconds, "source": "targeted-pr"}
        self.cache.set(repo, updated)
        return updated

    async def pull_detail(self, repo: str, number: int) -> dict[str, Any]:
        if repo not in configured_repositories():
            raise ValueError("Repository is not configured")
        if number <= 0:
            raise ValueError("Invalid pull request number")
        owner, name = repo.split("/", 1)
        root = f"/repos/{owner}/{name}"
        async with httpx.AsyncClient(timeout=25.0) as client:
            detail, _, _ = await self._get(client, f"{root}/pulls/{number}")
            head_sha = ((detail.get("head") or {}).get("sha") or "").strip()
            paths = [
                f"{root}/issues/{number}/comments?per_page=100",
                f"{root}/pulls/{number}/comments?per_page=100",
                f"{root}/pulls/{number}/reviews?per_page=100",
                f"{root}/pulls/{number}/commits?per_page=100",
                f"{root}/issues/{number}/timeline?per_page=100",
            ]
            results = await asyncio.gather(*(self._get(client, path) for path in paths), return_exceptions=True)

            def payload(index: int) -> Any:
                result = results[index]
                return [] if isinstance(result, Exception) else result[0]

            issue_comments = payload(0)
            review_comments = payload(1)
            reviews = payload(2)
            commits = payload(3)
            timeline = payload(4)
            checks: list[dict[str, Any]] = []
            if head_sha:
                try:
                    check_payload, _, _ = await self._get(client, f"{root}/commits/{head_sha}/check-runs?per_page=100")
                    checks = check_payload.get("check_runs", []) if isinstance(check_payload, dict) else []
                except RuntimeError:
                    checks = []

        normalized = normalize_pull(detail, reviews if isinstance(reviews, list) else [], checks, commits if isinstance(commits, list) else [])
        normalized.pop("_commits", None)
        return {
            **normalized,
            "body": detail.get("body") or "",
            "comments": [normalize_issue_comment(item) for item in issue_comments if isinstance(item, dict)],
            "reviewComments": [normalize_review_comment(item) for item in review_comments if isinstance(item, dict)],
            "reviews": [normalize_review(item) for item in reviews if isinstance(item, dict)],
            "commits": [normalize_pr_commit(item) for item in commits if isinstance(item, dict)],
            "events": normalize_pull_events(timeline if isinstance(timeline, list) else []),
            "checks": [normalize_check(item) for item in checks if isinstance(item, dict)],
            "stats": {
                "commits": detail.get("commits", len(commits) if isinstance(commits, list) else 0),
                "additions": detail.get("additions", 0),
                "deletions": detail.get("deletions", 0),
                "changedFiles": detail.get("changed_files", 0),
                "comments": detail.get("comments", len(issue_comments) if isinstance(issue_comments, list) else 0),
                "reviewComments": detail.get("review_comments", len(review_comments) if isinstance(review_comments, list) else 0),
            },
        }

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
    merged_at = detail.get("merged_at")
    lifecycle = "merged" if merged_at else ("closed" if detail.get("state") == "closed" else "open")
    return {
        "number": detail["number"],
        "title": detail.get("title", ""),
        "state": detail.get("state", "open"),
        "lifecycle": lifecycle,
        "url": detail.get("html_url", ""),
        "body": detail.get("body") or "",
        "author": (detail.get("user") or {}).get("login", "unknown"),
        "base": (detail.get("base") or {}).get("ref", "main"),
        "baseSha": (detail.get("base") or {}).get("sha", ""),
        "head": (detail.get("head") or {}).get("ref", "unknown"),
        "headSha": (detail.get("head") or {}).get("sha", ""),
        "mergeCommitSha": detail.get("merge_commit_sha"),
        "draft": bool(detail.get("draft")),
        "mergeable": detail.get("mergeable"),
        "mergeableState": detail.get("mergeable_state"),
        "createdAt": detail.get("created_at"),
        "updatedAt": detail.get("updated_at"),
        "closedAt": detail.get("closed_at"),
        "mergedAt": merged_at,
        "commentsCount": detail.get("comments", 0),
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


def derive_pull_relations(pulls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_number = {pull["number"]: pull for pull in pulls}
    by_head = {pull.get("head"): pull for pull in pulls if pull.get("head")}
    by_head_sha = {pull.get("headSha"): pull for pull in pulls if pull.get("headSha")}
    edges: dict[tuple[int, int], dict[str, Any]] = {}

    def add(source: int, target: int, kind: str, reason: str) -> None:
        if source == target or source not in by_number or target not in by_number:
            return
        key = (source, target)
        current = edges.get(key)
        if current is None or current["kind"] == "mentioned":
            edges[key] = {"source": source, "target": target, "kind": kind, "reason": reason}

    dependency_pattern = re.compile(
        r"(?:depends?\s+on|blocked\s+by|stacked\s+on|based\s+on|after|depends-on|requires?)\s+(?:pr\s*)?#(\d+)",
        re.IGNORECASE,
    )
    for pull in pulls:
        upstream = by_head.get(pull.get("base"))
        if upstream:
            add(upstream["number"], pull["number"], "stacked", f"{pull.get('base')} is PR #{upstream['number']} head")
        upstream_sha = by_head_sha.get(pull.get("baseSha"))
        if upstream_sha:
            add(upstream_sha["number"], pull["number"], "stacked", "base SHA matches upstream PR head")
        text = f"{pull.get('title', '')}\n{pull.get('body', '')}"
        for match in dependency_pattern.finditer(text):
            dependency = int(match.group(1))
            add(dependency, pull["number"], "mentioned", f"dependency reference to #{dependency}")
    return sorted(edges.values(), key=lambda edge: (edge["source"], edge["target"]))


def normalize_check(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": check.get("name", "check"),
        "status": check.get("status", "queued"),
        "conclusion": check.get("conclusion"),
        "url": check.get("html_url") or check.get("details_url"),
        "startedAt": check.get("started_at"),
        "completedAt": check.get("completed_at"),
    }


def normalize_review(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": review.get("id"),
        "user": (review.get("user") or {}).get("login", "unknown"),
        "state": review.get("state", "COMMENTED"),
        "body": review.get("body") or "",
        "submittedAt": review.get("submitted_at"),
        "commitId": review.get("commit_id"),
        "url": review.get("html_url"),
    }


def normalize_issue_comment(comment: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": comment.get("id"),
        "user": (comment.get("user") or {}).get("login", "unknown"),
        "body": comment.get("body") or "",
        "createdAt": comment.get("created_at"),
        "updatedAt": comment.get("updated_at"),
        "url": comment.get("html_url"),
    }


def normalize_review_comment(comment: dict[str, Any]) -> dict[str, Any]:
    return {
        **normalize_issue_comment(comment),
        "path": comment.get("path"),
        "line": comment.get("line") or comment.get("original_line"),
        "side": comment.get("side"),
        "commitId": comment.get("commit_id"),
        "diffHunk": comment.get("diff_hunk") or "",
    }


def normalize_pr_commit(commit: dict[str, Any]) -> dict[str, Any]:
    data = commit.get("commit") or {}
    author = data.get("author") or {}
    return {
        "sha": commit.get("sha", ""),
        "message": data.get("message") or "Commit",
        "author": author.get("name") or (commit.get("author") or {}).get("login", "unknown"),
        "timestamp": author.get("date"),
        "url": commit.get("html_url"),
        "parents": [item.get("sha", "") for item in commit.get("parents", []) if item.get("sha")],
    }


def normalize_pull_events(timeline: list[dict[str, Any]]) -> list[dict[str, Any]]:
    useful = {
        "merged", "closed", "reopened", "head_ref_force_pushed", "ready_for_review",
        "convert_to_draft", "base_ref_changed", "head_ref_deleted", "head_ref_restored",
        "renamed", "labeled", "unlabeled", "review_requested", "review_request_removed",
    }
    events: list[dict[str, Any]] = []
    for item in timeline:
        event = item.get("event")
        if event not in useful:
            continue
        events.append({
            "id": item.get("id") or f"{event}-{item.get('created_at')}",
            "event": event,
            "createdAt": item.get("created_at"),
            "actor": (item.get("actor") or {}).get("login", "unknown"),
            "commitId": item.get("commit_id") or item.get("sha"),
            "label": (item.get("label") or {}).get("name"),
            "requestedReviewer": (item.get("requested_reviewer") or {}).get("login"),
            "rename": item.get("rename"),
        })
    return events


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
