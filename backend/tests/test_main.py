from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_root_returns_service_info() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {
        "name": "NYC Rat Race API",
        "docs": "/docs",
    }


def test_health_check() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "nyc-rat-race-api",
    }
