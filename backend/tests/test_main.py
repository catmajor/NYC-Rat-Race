from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

client = TestClient(create_app())


def test_root_serves_frontend_when_built() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>Rat Race" in response.text


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "nyc-rat-race-api",
    }


def test_root_fallback_when_dist_missing(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    fallback_client = TestClient(create_app(missing))
    response = fallback_client.get("/")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "NYC Rat Race API"
    assert "dist" in body["hint"]


def test_root_serves_from_temporary_dist(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<!doctype html><title>Temp</title>", encoding="utf-8")
    dist_client = TestClient(create_app(tmp_path))
    response = dist_client.get("/")

    assert response.status_code == 200
    assert "Temp" in response.text