from fastapi.testclient import TestClient

from backend.app.main import app


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
