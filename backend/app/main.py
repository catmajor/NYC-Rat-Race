from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .advisers import GossipAgent, GrandpaAgent, StormyAgent, TwitchAgent
from .config import (
    get_analogue_store,
    get_narrative_generator,
    get_point_in_time_signals,
)
from .models import AdviserResponse, HistoricalState


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


def _run_adviser(adviser_id: str, request: AdviserRequest) -> AdviserResponse:
    """Run one specialized adviser against the same point-in-time state."""
    agent_class = ADVISER_AGENTS.get(adviser_id)
    if agent_class is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown adviser {adviser_id!r}. Choose from {sorted(ADVISER_AGENTS)}.",
        )
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
    return agent_class(
        store,
        data_source=data_source,
        narrative_generator=narrative_generator,
    ).advise(
        state,
        idle_taxis_by_zone=request.idle_taxis_by_zone,
        max_matches=request.max_matches,
    )


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