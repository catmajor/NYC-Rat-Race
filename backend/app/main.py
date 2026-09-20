from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .advisers import GossipAgent, GrandpaAgent, StormyAgent, TwitchAgent
from .config import (
    get_analogue_store,
    get_narrative_generator,
    get_point_in_time_signals,
)
from .data import real_weekday_mean
from .game import GameSession, SESSION, ZONE_IDS
from .models import AdviserResponse, HistoricalState


_ADVISER_CACHE_MAX = 512
_adviser_cache: Dict[str, AdviserResponse] = {}
_game_sessions: Dict[str, GameSession] = {"default": SESSION}


class HealthResponse(BaseModel):
    status: str
    service: str


class AdviserRequest(BaseModel):
    timestamp: datetime
    demand_by_zone: Optional[Dict[str, float]] = None
    weather: Dict[str, float] = Field(default_factory=dict)
    events: Dict[str, float] = Field(default_factory=dict)
    idle_taxis_by_zone: Dict[str, int] = Field(default_factory=dict)
    max_matches: int = Field(default=8, ge=1, le=50)


class GameAdvanceRequest(BaseModel):
    day: int = Field(ge=1, le=3)
    allocation: Dict[str, int]
    # ``turn`` is the public name. Accept ``round`` for older clients during
    # the transition away from the round-based UI.
    turn: Optional[int] = Field(default=None, ge=1, le=4)
    round: Optional[int] = Field(default=None, ge=1, le=4)


class GameTimeoutRequest(BaseModel):
    day: int = Field(ge=1, le=3)
    turn: Optional[int] = Field(default=None, ge=1, le=4)
    round: Optional[int] = Field(default=None, ge=1, le=4)


def _requested_turn(turn: Optional[int], legacy_round: Optional[int]) -> int:
    """Resolve the new turn field while keeping old clients compatible."""
    if turn is not None and legacy_round is not None and turn != legacy_round:
        raise HTTPException(status_code=422, detail="turn and round must refer to the same window")
    requested = turn if turn is not None else legacy_round
    if requested is None:
        raise HTTPException(status_code=422, detail="turn is required")
    return requested


# The Don is intentionally not listed here yet: the canonical MVP spec makes
# it a later mixture-of-experts adviser built on the four individual rats.
ADVISER_AGENTS = {
    "twitch": TwitchAgent,
    "stormy": StormyAgent,
    "grandpa": GrandpaAgent,
    "gossip": GossipAgent,
}

ADVISER_METADATA = {
    "twitch": {
        "name": "Twitch",
        "specialty": "mobility_momentum",
        "tools": [
            "get_recent_demand",
            "calculate_momentum",
            "compare_neighboring_zones",
            "flow_propagation",
        ],
    },
    "stormy": {
        "name": "Stormy",
        "specialty": "weather",
        "tools": [
            "get_weather",
            "compare_weather_condition",
            "get_historical_weather_effect",
        ],
    },
    "grandpa": {
        "name": "Grandpa",
        "specialty": "historical_analogues",
        "tools": ["find_similar_periods", "get_outcomes_for_similar_periods"],
    },
    "gossip": {
        "name": "Gossip",
        "specialty": "events_news",
        "tools": [
            "get_event_activity",
            "get_event_intensity",
            "get_nearby_events",
            "historical_event_effect",
        ],
    },
}


def _game_for(session_id: Optional[str]) -> GameSession:
    """Keep browser sessions isolated on warm server instances."""
    key = (session_id or "default").strip()[:128] or "default"
    session = _game_sessions.get(key)
    if session is None:
        session = GameSession()
        _game_sessions[key] = session
    return session


def _game_state(session: GameSession) -> Dict[str, object]:
    """Return the game state with store-derived regional context."""
    state = session.state()
    zones = state["zones"]
    assert isinstance(zones, dict)
    try:
        means = real_weekday_mean(session.timestamp)
        state["historic_mean_source"] = "tlc-weekday-store"
    except RuntimeError:
        # The ONNX game is playable without the optional TLC pickup parquet.
        # Keep the same state contract and use the current point-in-time
        # baseline until the store is available.
        means = {
            zone_id: float(zone["baseline_demand"])
            for zone_id, zone in zones.items()
            if isinstance(zone, dict)
        }
        state["historic_mean_source"] = "model-baseline-fallback"
    for zone_id in ZONE_IDS:
        zone = zones[zone_id]
        assert isinstance(zone, dict)
        zone["historic_mean"] = round(
            means[zone_id], 1
        )

    state["weekday_label"] = session.timestamp.strftime("%A").upper()
    return state


def _fingerprint(request: AdviserRequest) -> str:
    """Collapse the full adviser input into a stable cache key.

    Timestamp gives temporal scoping; the numeric state dictionaries capture the
    point-in-time analyst context. This keeps re-renders and page reloads from
    burning free-tier Gemini quota or adding latency.
    """

    def sorted_items(values: Optional[Dict[str, float]]) -> list:
        if not values:
            return []
        return [(str(key), round(float(value), 6)) for key, value in sorted(values.items())]

    parts = [
        request.timestamp.isoformat(),
        str(request.max_matches),
        "|".join(f"{key}={value}" for key, value in sorted_items(request.demand_by_zone)),
        "|".join(f"{key}={value}" for key, value in sorted_items(request.weather)),
        "|".join(f"{key}={value}" for key, value in sorted_items(request.events)),
        "|".join(f"{key}={value}" for key, value in sorted_items(request.idle_taxis_by_zone)),
    ]
    return "|".join(parts)


def _run_adviser(adviser_id: str, request: AdviserRequest) -> AdviserResponse:
    """Run one specialized adviser against the same point-in-time state."""
    agent_class = ADVISER_AGENTS.get(adviser_id)
    if agent_class is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown adviser {adviser_id!r}. Choose from {sorted(ADVISER_AGENTS)}.",
        )

    cache_key = f"{adviser_id}:{_fingerprint(request)}"
    if cache_key in _adviser_cache:
        return _adviser_cache[cache_key]

    try:
        store, data_source = get_analogue_store()
        narrative_generator = get_narrative_generator()
        stored_state = store.state_at(request.timestamp)
        live_weather = {}
        live_events = {}
        if data_source.startswith("tlc-"):
            signals = get_point_in_time_signals()
            live_weather = signals.weather_at(request.timestamp)
            live_events = signals.events_at(request.timestamp)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    demand_by_zone = request.demand_by_zone
    if demand_by_zone is None and stored_state is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "No TLC state exists at this timestamp. Supply demand_by_zone "
                "or use a timestamp aligned to a configured 3-hour state."
            ),
        )

    state = HistoricalState(
        timestamp=request.timestamp,
        demand_by_zone=(
            demand_by_zone
            if demand_by_zone is not None
            else stored_state.demand_by_zone
        ),
        weather={
            **live_weather,
            **(stored_state.weather if stored_state else {}),
            **request.weather,
        },
        events={
            **live_events,
            **(stored_state.events if stored_state else {}),
            **request.events,
        },
    )
    response = agent_class(
        store,
        data_source=data_source,
        narrative_generator=narrative_generator,
    ).advise(
        state,
        idle_taxis_by_zone=request.idle_taxis_by_zone,
        max_matches=request.max_matches,
    )
    _adviser_cache[cache_key] = response
    if len(_adviser_cache) > _ADVISER_CACHE_MAX:
        _adviser_cache.clear()
    return response


def create_app(frontend_dist: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="NYC Rat Race API",
        description="API + static frontend for the NYC Rat Race project.",
        version="0.1.0",
    )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health_check() -> HealthResponse:
        """Report whether the API is ready to receive requests."""
        return HealthResponse(status="ok", service="nyc-rat-race-api")

    @app.post(
        "/api/advisers/grandpa",
        response_model=AdviserResponse,
        tags=["advisers"],
    )
    def run_grandpa(request: AdviserRequest) -> AdviserResponse:
        """Backward-compatible Grandpa route."""
        return _run_adviser("grandpa", request)

    @app.post(
        "/api/advisers/{adviser_id}",
        response_model=AdviserResponse,
        tags=["advisers"],
    )
    def run_adviser(adviser_id: str, request: AdviserRequest) -> AdviserResponse:
        """Run any of the four specialized MVP adviser rats."""
        return _run_adviser(adviser_id, request)

    @app.get("/api/advisers", tags=["advisers"])
    def list_advisers() -> List[Dict[str, str]]:
        """Return the stable adviser metadata used by the frontend."""
        return [
            {
                "id": adviser_id,
                "name": metadata["name"],
                "specialty": metadata["specialty"],
                "endpoint": f"/api/advisers/{adviser_id}",
                "tools": ",".join(metadata["tools"]),
            }
            for adviser_id, metadata in ADVISER_METADATA.items()
        ]

    @app.get("/api/game/state", tags=["game"])
    def game_state(
        session_id: Optional[str] = Header(default=None, alias="X-Rat-Race-Session"),
    ) -> Dict[str, object]:
        """Return the current decision state for the local Rat Cab session."""
        return _game_state(_game_for(session_id))

    @app.post("/api/game/reset", tags=["game"])
    def reset_game(
        session_id: Optional[str] = Header(default=None, alias="X-Rat-Race-Session"),
    ) -> Dict[str, object]:
        """Reset the local deterministic simulation to day one."""
        session = _game_for(session_id)
        session.reset()
        return _game_state(session)

    @app.post("/api/game/advance", tags=["game"])
    def advance_game(
        request: GameAdvanceRequest,
        session_id: Optional[str] = Header(default=None, alias="X-Rat-Race-Session"),
    ) -> Dict[str, object]:
        """Score one allocation, hide the outcome, and move to the next turn."""
        session = _game_for(session_id)
        requested_turn = _requested_turn(request.turn, request.round)
        if request.day != session.day or requested_turn != session.round_number:
            raise HTTPException(status_code=409, detail="This window is no longer current")
        try:
            result = session.advance(request.allocation)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"result": result, "state": _game_state(session), "zones": list(ZONE_IDS)}

    @app.post("/api/game/timeout", tags=["game"])
    def timeout_game(
        request: GameTimeoutRequest,
        session_id: Optional[str] = Header(default=None, alias="X-Rat-Race-Session"),
    ) -> Dict[str, object]:
        """End the current run when the player misses the decision window."""
        session = _game_for(session_id)
        requested_turn = _requested_turn(request.turn, request.round)
        if request.day != session.day or requested_turn != session.round_number:
            raise HTTPException(status_code=409, detail="This window is no longer current")
        try:
            session.timeout()
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"state": _game_state(session), "zones": list(ZONE_IDS)}

    if frontend_dist is None:
        frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"

    if frontend_dist.is_dir():
        # Registered last so /health, /docs, /openapi.json still win.
        app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="frontend")
    else:
        @app.get("/", tags=["system"])
        def read_root() -> dict[str, str]:
            """Return basic service information."""
            return {
                "name": "NYC Rat Race API",
                "docs": "/docs",
                "hint": "frontend/dist not built; run `npm run build` in frontend/ and restart.",
            }

    return app


app = create_app()
