from datetime import datetime, timedelta

from app.advisers import GrandpaAgent, GrandpaRat
from app.data import InMemoryAnalogueStore
from app.llm import AdviserDecisionResult
from app.models import HistoricalEpisode, HistoricalState, ZONE_IDS


def _episode(timestamp: datetime, favored_zone: str) -> HistoricalEpisode:
    current = {zone_id: 10.0 for zone_id in ZONE_IDS}
    current[favored_zone] = 45.0
    future = {zone_id: 8.0 for zone_id in ZONE_IDS}
    future[favored_zone] = 80.0
    return HistoricalEpisode(
        state=HistoricalState(timestamp=timestamp, demand_by_zone=current),
        next_demand_by_zone=future,
        next_fare_total=1000.0,
    )


def test_grandpa_uses_similar_periods_to_recommend_a_zone() -> None:
    target = datetime(2024, 10, 4, 8, 0)
    store = InMemoryAnalogueStore(
        [
            _episode(target - timedelta(days=7), "midtown"),
            _episode(target - timedelta(days=14), "midtown"),
            _episode(target - timedelta(days=21), "downtown"),
        ]
    )

    recommendation = GrandpaRat(store).recommend(
        HistoricalState(
            timestamp=target,
            demand_by_zone={zone_id: 10.0 for zone_id in ZONE_IDS},
        ),
        idle_taxis_by_zone={"upper_west": 10, "midtown": 2},
    )

    assert recommendation.name == "Grandpa"
    assert recommendation.matches_considered == 3
    assert "Midtown" in recommendation.short_recommendation
    assert recommendation.confidence > 0
    assert recommendation.evidence[0]["type"] == "analogue_count"
    assert recommendation.recommended_moves
    assert recommendation.recommended_moves[0].to_zone == "midtown"


def test_grandpa_returns_low_confidence_without_history() -> None:
    recommendation = GrandpaRat(InMemoryAnalogueStore()).recommend(
        HistoricalState(
            timestamp=datetime(2024, 10, 4, 8, 0),
            demand_by_zone={zone_id: 10.0 for zone_id in ZONE_IDS},
        )
    )

    assert recommendation.confidence == 0.0
    assert recommendation.recommended_moves == []


def test_grandpa_agent_returns_standardized_natural_language_response() -> None:
    target = datetime(2024, 10, 4, 8, 0)
    store = InMemoryAnalogueStore(
        [_episode(target - timedelta(days=7 * index), "midtown") for index in range(1, 4)]
    )

    response = GrandpaAgent(store, data_source="tlc-yellow").advise(
        HistoricalState(
            timestamp=target,
            demand_by_zone={zone_id: 10.0 for zone_id in ZONE_IDS},
        ),
        idle_taxis_by_zone={"upper_west": 10, "midtown": 2},
    )

    assert response.schema_version == "1.0"
    assert response.adviser_id == "grandpa"
    assert response.data_source == "tlc-yellow"
    assert response.recommendation.startswith("I compared 3 similar")
    assert response.forecast.horizon_hours == 3
    assert response.evidence[0].kind == "analogue_count"
    assert response.tools_used


def test_grandpa_agent_can_use_shared_llm_narrative_generator() -> None:
    class FakeNarrativeGenerator:
        source = "test-llm"

        def generate(self, context):
            assert context["horizon_hours"] == 3
            assert context["forecast"]["predicted_revenue"] == 1000.0
            return "Grandpa says Midtown has the clearest historical signal."

        def decide(self, context):
            assert context["horizon_hours"] == 3
            assert context["forecast"]["predicted_revenue"] == 1000.0
            return AdviserDecisionResult(
                text="Grandpa says Midtown has the clearest historical signal.",
                confidence=0.85,
            )

    target = datetime(2024, 10, 4, 8, 0)
    store = InMemoryAnalogueStore(
        [_episode(target - timedelta(days=7), "midtown")]
    )
    response = GrandpaAgent(
        store,
        narrative_generator=FakeNarrativeGenerator(),
    ).advise(
        HistoricalState(
            timestamp=target,
            demand_by_zone={zone_id: 10.0 for zone_id in ZONE_IDS},
        )
    )

    assert response.narrative_source == "test-llm"
    assert response.recommendation.startswith("Grandpa says")
    assert response.confidence == 0.85
