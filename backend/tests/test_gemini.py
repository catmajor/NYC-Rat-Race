"""Gemini adviser-path tests that require no network access.

The Gemini constructor and SDK import are bypassed so these run even when the
google-genai package is not installed; each test drives the client-shaped
object and config builder directly.
"""

from datetime import datetime

from app.advisers import StormyAgent
from app.data import build_demo_analogue_store
from app.llm import AdviserDecision, GeminiNarrativeGenerator


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeClient:
    def __init__(self, decision: AdviserDecision) -> None:
        self.last_config = None
        self.models = _FakeModels(decision, self)


class _FakeModels:
    def __init__(self, decision: AdviserDecision, owner: "_FakeClient") -> None:
        self._decision = decision
        self._owner = owner

    def generate_content(self, model: str, contents: str, config) -> _FakeResponse:
        self._owner.last_config = config
        return _FakeResponse(self._decision.model_dump_json())


def _generator(decision: AdviserDecision) -> GeminiNarrativeGenerator:
    generator = object.__new__(GeminiNarrativeGenerator)
    generator.client = _FakeClient(decision)
    generator.model = "gemini-test"
    generator.temperature = 0.8
    generator.timeout_seconds = 20.0
    generator.source = "gemini:gemini-test"
    generator._build_config = lambda system_instruction: {
        "system_instruction": system_instruction,
        "response_mime_type": "application/json",
        "response_schema": AdviserDecision,
        "temperature": generator.temperature,
    }
    return generator


def _demo_state():
    store = build_demo_analogue_store()
    state = store.state_at(datetime(2024, 10, 4, 8, 0))
    assert state is not None
    return store, state


def test_gemini_generator_parses_structured_decision_and_uses_json_config() -> None:
    store, state = _demo_state()

    agent = StormyAgent(
        store,
        data_source="synthetic-demo",
        narrative_generator=_generator(
            AdviserDecision(advice="Stormy fears a rainy crosstown frenzy.", confidence=0.77)
        ),
    )
    response = agent.advise(
        state,
        idle_taxis_by_zone={"midtown": 10, "downtown": 3},
    )

    assert response.narrative_source == "gemini:gemini-test"
    assert response.recommendation == "Stormy fears a rainy crosstown frenzy."
    assert response.confidence == 0.77
    assert response.forecast.demand_by_zone


def test_gemini_failure_falls_back_to_deterministic_template() -> None:
    store, state = _demo_state()

    class _BoomGenerator:
        source = "gemini:boom"

        def decide(self, context):
            raise RuntimeError("gemini outage")

    agent = StormyAgent(
        store,
        data_source="synthetic-demo",
        narrative_generator=_BoomGenerator(),
    )
    response = agent.advise(state)

    assert response.narrative_source == "template-fallback"
    assert response.recommendation
    assert 0.0 <= response.confidence <= 1.0


class _FakeRawClient:
    """Client that returns raw JSON rather than an already-validated model."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.last_config = None
        self.models = _FakeRawModels(self)


class _FakeRawModels:
    def __init__(self, owner: "_FakeRawClient") -> None:
        self._owner = owner

    def generate_content(self, model: str, contents: str, config) -> _FakeResponse:
        self._owner.last_config = config
        return _FakeResponse(self._owner.text)


def test_gemini_falls_back_when_confidence_is_out_of_range() -> None:
    store, state = _demo_state()
    generator = object.__new__(GeminiNarrativeGenerator)
    generator.client = _FakeRawClient('{"advice": "Too confident.", "confidence": 7.5}')
    generator.model = "gemini-test"
    generator.temperature = 0.8
    generator.timeout_seconds = 20.0
    generator.source = "gemini:gemini-test"
    generator._build_config = lambda system_instruction: {}

    agent = StormyAgent(
        store,
        data_source="synthetic-demo",
        narrative_generator=generator,
    )
    response = agent.advise(state)

    assert response.narrative_source == "template-fallback"


def test_gemini_generator_uses_structured_output_config() -> None:
    generator = _generator(AdviserDecision(advice="Signal here.", confidence=0.5))
    generator.decide({"adviser": {"name": "Stormy", "specialty": "weather", "personality": "wet"}})

    config = generator.client.last_config
    assert config["response_mime_type"] == "application/json"
    assert config["response_schema"] is AdviserDecision
    assert "Stormy" in config["system_instruction"]