import time

import httpx
import pytest

from backend.app.github import (
    GitHubAggregator,
    GitHubRateLimitError,
    SnapshotCache,
    configured_repositories,
    normalize_commit,
    normalize_pull,
)


def test_configured_repositories(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORIES", "A/one, B/two")
    assert configured_repositories() == ["A/one", "B/two"]


def test_snapshot_cache_hit_and_expiry():
    cache = SnapshotCache(ttl_seconds=5)
    cache.set("A/one", {"value": 1})
    assert cache.get("A/one") == {"value": 1}
    cache._items["A/one"].expires_at = time.monotonic() - 1
    assert cache.get("A/one") is None


def test_normalize_pull_and_commit():
    detail = {
        "number": 7,
        "title": "Stacked change",
        "state": "open",
        "html_url": "https://github.com/A/one/pull/7",
        "user": {"login": "dev"},
        "base": {"ref": "feature/base"},
        "head": {"ref": "feature/next", "sha": "abc"},
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "created_at": "2026-08-17T00:00:00Z",
        "updated_at": "2026-08-18T00:00:00Z",
        "requested_reviewers": [{"login": "reviewer"}],
        "labels": [{"name": "frontend"}],
    }
    commits = [{"sha": "abc", "commit": {"message": "hello\nbody", "author": {"name": "Dev", "date": "2026-08-18T00:00:00Z"}}, "parents": []}]
    pull = normalize_pull(detail, [{"user": {"login": "human", "type": "User"}, "state": "APPROVED"}], [{"name": "CI", "status": "completed", "conclusion": "success"}], commits)
    assert pull["requestedReviewers"] == ["reviewer"]
    assert pull["labels"] == ["frontend"]
    assert pull["commitCount"] == 1
    commit = normalize_commit(commits[0], "feature/next", 7)
    assert commit["message"] == "hello"
    assert commit["prNumber"] == 7


@pytest.mark.asyncio
async def test_conditional_get_reuses_payload_on_304(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_FLOW_STATE_DIR", str(tmp_path))
    for key in ("GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_BASE64", "GITHUB_APP_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(key, raising=False)
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(200, json={"value": 1}, headers={"etag": '"revision-1"'})
        assert request.headers["if-none-match"] == '"revision-1"'
        return httpx.Response(304)

    aggregator = GitHubAggregator(token="test", cache=SnapshotCache())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first, _, _ = await aggregator._get(client, "/repos/A/one")
        second, _, _ = await aggregator._get(client, "/repos/A/one")
    assert first == second == {"value": 1}
    assert requests == 2


@pytest.mark.asyncio
async def test_rate_limit_opens_global_rest_circuit(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_FLOW_STATE_DIR", str(tmp_path))
    for key in ("GITHUB_APP_ID", "GITHUB_APP_INSTALLATION_ID", "GITHUB_APP_PRIVATE_KEY", "GITHUB_APP_PRIVATE_KEY_BASE64", "GITHUB_APP_PRIVATE_KEY_PATH"):
        monkeypatch.delenv(key, raising=False)
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            403,
            text="API rate limit exceeded",
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(int(time.time()) + 120)},
        )

    aggregator = GitHubAggregator(token="test", cache=SnapshotCache())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(GitHubRateLimitError):
            await aggregator._get(client, "/repos/A/one")
        with pytest.raises(GitHubRateLimitError):
            await aggregator._get(client, "/repos/B/two")
    assert requests == 1
    assert aggregator.rate_limit_status()["paused"] is True


@pytest.mark.asyncio
async def test_incomplete_github_app_config_refuses_pat_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_FLOW_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.delenv("GITHUB_APP_INSTALLATION_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_BASE64", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    aggregator = GitHubAggregator(token="personal-token", cache=SnapshotCache())
    assert aggregator.authentication_mode == "github-app-misconfigured"
    with pytest.raises(RuntimeError, match="refusing PAT fallback"):
        await aggregator._headers()


def test_review_webhook_patches_cached_pull_without_rest(monkeypatch, tmp_path):
    monkeypatch.setenv("DEV_FLOW_STATE_DIR", str(tmp_path))
    cache = SnapshotCache()
    cache.set("A/one", {
        "repository": "A/one",
        "defaultBranch": "main",
        "pulls": [{"number": 7, "reviews": [], "checks": [], "head": "feature", "headSha": "abc"}],
        "pullRelations": [],
        "commits": [],
    })
    aggregator = GitHubAggregator(token="test", cache=cache)
    patched = aggregator.apply_webhook("A/one", "pull_request_review", {
        "action": "submitted",
        "review": {"state": "approved", "body": "LGTM", "user": {"login": "alice", "type": "User"}},
    }, 7)
    assert patched is True
    pull = cache.get("A/one")["pulls"][0]
    assert pull["reviews"][0]["state"] == "APPROVED"
    assert pull["reviews"][0]["user"] == "alice"
