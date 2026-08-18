import os
import time

from backend.app.github import SnapshotCache, configured_repositories, normalize_commit, normalize_pull


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

