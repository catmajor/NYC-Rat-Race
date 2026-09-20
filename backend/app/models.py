"""Domain models shared by the adviser and data layers."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Mapping, Optional, Tuple

from pydantic import BaseModel, Field


ZONE_IDS: Tuple[str, ...] = (
    "harlem",
    "upper_west",
    "upper_east",
    "midtown",
    "downtown",
    "north_brooklyn",
    "south_brooklyn",
    "queens_west",
    "airports",
    "queens_east",
    "bronx",
    "staten_island",
)


@dataclass(frozen=True)
class HistoricalState:
    """Information that would have been available at an analogue timestamp."""

    timestamp: datetime
    demand_by_zone: Mapping[str, float]
    weather: Mapping[str, float] = field(default_factory=dict)
    events: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class HistoricalEpisode:
    """A historical state paired with its observed next-three-hour outcome."""

    state: HistoricalState
    next_demand_by_zone: Mapping[str, float]
    next_fare_total: float = 0.0
    source: str = "synthetic"


@dataclass(frozen=True)
class AnalogueMatch:
    """A ranked historical episode returned to an adviser."""

    timestamp: datetime
    distance: float
    similarity: float
    next_demand_by_zone: Mapping[str, float]
    next_fare_total: float
    source: str


class AdviserMove(BaseModel):
    """Standardized move recommendation shared by every adviser."""

    from_zone: str
    to_zone: str
    taxi_count: int = Field(ge=1)


class AdviserEvidence(BaseModel):
    """Frontend-friendly, typed explanation attached to an adviser response."""

    kind: str
    label: str
    detail: str
    zone_id: Optional[str] = None
    value: Optional[float] = None
    unit: Optional[str] = None
    timestamp: Optional[datetime] = None
    source: Optional[str] = None


class AdviserForecast(BaseModel):
    """The compact forecast portion of a standardized adviser response."""

    horizon_hours: int = Field(default=3, ge=1)
    demand_by_zone: Dict[str, float]
    predicted_revenue: float = 0.0


class AdviserResponse(BaseModel):
    """Stable JSON contract for Twitch, Stormy, Grandpa, Gossip, and The Don."""

    schema_version: str = "1.0"
    adviser_id: str
    adviser_name: str
    specialty: str
    as_of: datetime
    horizon_hours: int = Field(default=3, ge=1)
    recommendation: str
    confidence: float = Field(ge=0.0, le=1.0)
    moves: List[AdviserMove] = Field(default_factory=list)
    evidence: List[AdviserEvidence] = Field(default_factory=list)
    forecast: AdviserForecast
    matches_considered: int = Field(default=0, ge=0)
    data_source: str
    narrative_source: str = "template"
    tools_used: List[str] = Field(default_factory=list)
