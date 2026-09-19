from datetime import datetime

from fastapi.testclient import TestClient

from app.advisers import GossipAgent, StormyAgent, TwitchAgent
from app.data import build_demo_analogue_store
from app.main import app


def _demo_state():
    store = build_demo_analogue_store()
    state = store.state_at(datetime(2024, 10, 4, 8, 0))
    assert state is not None
    return store, state


def test_specialist_rats_return_the_shared_contract_and_distinct_tools() -> None:
    store, state = _demo_state()
    agents = (TwitchAgent(store), StormyAgent(store), GossipAgent(store))

    responses = [
        agent.advise(
            state,
            idle_taxis_by_zone={"midtown": 10, "downtown": 3},
        )
        for agent in agents
    ]

    assert [response.adviser_id for response in responses] == [
        "twitch",
        "stormy",
        "gossip",
    ]
    for response in responses:
        assert response.schema_version == "1.0"
        assert response.horizon_hours == 3
        assert response.forecast.demand_by_zone
        assert response.evidence
        assert "build_reposition_plan" in response.tools_used
        assert "generate_natural_language_advice" in response.tools_used

    assert "calculate_momentum" in responses[0].tools_used
    assert "get_historical_weather_effect" in responses[1].tools_used
    assert "get_event_intensity" in responses[2].tools_used


def test_adviser_catalog_exposes_frontend_routes_and_tools() -> None:
    client = TestClient(app)
    response = client.get("/api/advisers")

    assert response.status_code == 200
    advisers = response.json()
    assert [adviser["id"] for adviser in advisers] == [
        "twitch",
        "stormy",
        "grandpa",
        "gossip",
    ]
    assert advisers[0]["endpoint"] == "/api/advisers/twitch"
    assert "calculate_momentum" in advisers[0]["tools"]


def test_specialist_route_uses_shared_response_shape(monkeypatch) -> None:
    store, _ = _demo_state()
    monkeypatch.setattr("app.main.get_analogue_store", lambda: (store, "synthetic-demo"))
    monkeypatch.setattr("app.main.get_narrative_generator", lambda: None)
    client = TestClient(app)

    response = client.post(
        "/api/advisers/stormy",
        json={
            "timestamp": "2024-10-04T08:00:00",
            "idle_taxis_by_zone": {"midtown": 10, "downtown": 3},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["adviser_id"] == "stormy"
    assert payload["narrative_source"] == "template"
    assert payload["forecast"]["horizon_hours"] == 3
