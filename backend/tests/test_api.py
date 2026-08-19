from fastapi.testclient import TestClient

from backend.app.main import _should_analyze_webhook, app


def test_health_endpoint(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORIES", "A/one")
    client = TestClient(app)
    for path in ("/api/health", "/dev_dashboard/api/health"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["repositories"] == ["A/one"]


def test_repositories_endpoint(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORIES", "A/one,B/two")
    client = TestClient(app)
    expected = {"repositories": ["A/one", "B/two"]}
    assert client.get("/api/repositories").json() == expected
    assert client.get("/dev_dashboard/api/repositories").json() == expected


def test_snapshot_rejects_unconfigured_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORIES", "A/one")
    client = TestClient(app)
    for path in ("/api/snapshot", "/dev_dashboard/api/snapshot"):
        response = client.get(path, params={"repo": "X/nope"})
        assert response.status_code == 400


def test_vertex_trigger_filters_bot_noise_but_keeps_human_technical_events():
    bot_comment = {
        "sender": {"login": "vercel[bot]", "type": "Bot"},
        "comment": {"body": "Deployment ready"},
    }
    human_ack = {
        "sender": {"login": "alice", "type": "User"},
        "comment": {"body": "확인했습니다"},
    }
    human_technical = {
        "sender": {"login": "alice", "type": "User"},
        "comment": {"body": "API contract 위반이 있어 수정이 필요합니다"},
    }
    human_approval = {
        "sender": {"login": "alice", "type": "User"},
        "review": {"state": "approved", "body": ""},
    }

    assert _should_analyze_webhook(bot_comment, "issue_comment", "created") is False
    assert _should_analyze_webhook(human_ack, "issue_comment", "created") is False
    assert _should_analyze_webhook(human_technical, "issue_comment", "created") is True
    assert _should_analyze_webhook(human_approval, "pull_request_review", "submitted") is True
    assert _should_analyze_webhook(bot_comment, "check_run", "completed") is True
