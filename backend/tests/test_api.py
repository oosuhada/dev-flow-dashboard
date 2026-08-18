from fastapi.testclient import TestClient

from backend.app.main import app


def test_health_endpoint(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORIES", "A/one")
    client = TestClient(app)
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["repositories"] == ["A/one"]


def test_repositories_endpoint(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORIES", "A/one,B/two")
    client = TestClient(app)
    assert client.get("/api/repositories").json() == {"repositories": ["A/one", "B/two"]}


def test_snapshot_rejects_unconfigured_repo(monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORIES", "A/one")
    client = TestClient(app)
    response = client.get("/api/snapshot", params={"repo": "X/nope"})
    assert response.status_code == 400

