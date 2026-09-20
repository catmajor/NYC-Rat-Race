"""Authored GDELT-style event headlines gated by game conditions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import random
from typing import Dict, List, Mapping, Optional


@dataclass(frozen=True)
class EventTemplate:
    event_id: str
    headline: str
    zone: Optional[str]
    intensity: str
    tone: float
    priority: int
    min_temp: Optional[float] = None
    max_temp: Optional[float] = None
    min_rain: Optional[float] = None
    max_rain: Optional[float] = None
    min_wind: Optional[float] = None
    max_visibility: Optional[float] = None
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    min_gdelt_count: Optional[float] = None

    def matches(
        self,
        timestamp: datetime,
        weather: Mapping[str, float],
        gdelt_count: float,
    ) -> bool:
        temp = weather["temperature_c"]
        rain = weather["rain_mm"]
        wind = weather["wind_mps"]
        visibility = weather["visibility_km"]
        return all((
            self.min_temp is None or temp >= self.min_temp,
            self.max_temp is None or temp <= self.max_temp,
            self.min_rain is None or rain >= self.min_rain,
            self.max_rain is None or rain <= self.max_rain,
            self.min_wind is None or wind >= self.min_wind,
            self.max_visibility is None or visibility <= self.max_visibility,
            self.start_hour is None or timestamp.hour >= self.start_hour,
            self.end_hour is None or timestamp.hour <= self.end_hour,
            self.min_gdelt_count is None or gdelt_count >= self.min_gdelt_count,
        ))


EVENT_CATALOG: tuple[EventTemplate, ...] = (
    EventTemplate("rain_transit", "Rain is slowing curb activity around Midtown and Penn Station", "midtown", "high", -5, 90, min_rain=3),
    EventTemplate("downtown_hearing", "A City Hall hearing is pulling a dense crowd into Downtown", "downtown", "medium", -2, 70, start_hour=9, end_hour=18, min_gdelt_count=3),
    EventTemplate("jfk_wind", "Crosswinds are stacking departures around JFK", "airports", "high", -6, 95, min_wind=8),
    EventTemplate("jfk_fog", "Low visibility is slowing the airport pickup queue", "airports", "high", -8, 100, max_visibility=3),
    EventTemplate("bronx_school", "School dismissal is concentrating pickups across the Bronx", "bronx", "medium", 2, 50, start_hour=14, end_hour=17),
    EventTemplate("museum_pulse", "Museum traffic is spilling onto Upper East avenues", "upper_east", "medium", 6, 55, min_temp=18, max_rain=0.5, start_hour=10, end_hour=16),
    EventTemplate("park_weekend", "Clear weather is sending a weekend crowd through Upper West parks", "upper_west", "medium", 7, 45, min_temp=20, max_rain=0.5, start_hour=10, end_hour=17),
    EventTemplate("williamsburg_dining", "Outdoor dining demand is building in North Brooklyn", "north_brooklyn", "medium", 6, 45, min_temp=18, max_rain=0.5, start_hour=17, end_hour=21),
    EventTemplate("coney_beach", "Warm weather is drawing riders toward South Brooklyn waterfronts", "south_brooklyn", "medium", 7, 40, min_temp=22, max_rain=0.5, start_hour=11, end_hour=17),
    EventTemplate("queens_fair", "A local fair is generating short trips across Queens West", "queens_west", "medium", 5, 35, min_temp=19, max_rain=1, start_hour=11, end_hour=19, min_gdelt_count=2),
    EventTemplate("queens_east_clearout", "A road closure is redirecting evening demand into Queens East", "queens_east", "high", -5, 65, min_gdelt_count=4, start_hour=15, end_hour=20),
    EventTemplate("harlem_music", "A neighborhood music event is lifting evening traffic in Harlem", "harlem", "medium", 6, 45, min_temp=16, max_rain=1, start_hour=17, end_hour=22),
    EventTemplate("staten_ferry", "Ferry arrivals are producing a short Staten Island demand surge", "staten_island", "low", 4, 35, max_rain=2, start_hour=8, end_hour=20),
    EventTemplate("central_cold", "A cold snap is pushing pedestrians toward covered Midtown routes", "midtown", "medium", -4, 75, max_temp=2),
    EventTemplate("gdelt_negative", "A sharply negative news cycle is suppressing activity in the region", None, "high", -9, 85, min_gdelt_count=8),
    EventTemplate("gdelt_positive", "Positive local coverage is creating a visible demand pulse", None, "medium", 8, 50, min_gdelt_count=8),
)


def select_events(
    timestamp: datetime,
    weather_by_zone: Mapping[str, Mapping[str, float]],
    gdelt_events: Mapping[str, float],
) -> List[Dict[str, object]]:
    """Select at most one authored event per region for the current turn."""
    randomizer = random.Random(int(timestamp.strftime("%Y%m%d%H")))
    selected: Dict[str, EventTemplate] = {}
    for template in EVENT_CATALOG:
        if template.zone is None:
            eligible = [
                zone_id
                for zone_id, weather in weather_by_zone.items()
                if template.matches(
                    timestamp,
                    weather,
                    gdelt_events.get(f"zone_event_count:{zone_id}", gdelt_events.get(f"event_count:{zone_id}", 0.0)),
                )
            ]
            zones = [randomizer.choice(eligible)] if eligible else []
        else:
            zones = [template.zone]
        for zone_id in zones:
            if zone_id is None or zone_id not in weather_by_zone or zone_id in selected:
                continue
            count = gdelt_events.get(f"zone_event_count:{zone_id}", gdelt_events.get(f"event_count:{zone_id}", 0.0))
            if template.matches(timestamp, weather_by_zone[zone_id], count):
                current = selected.get(zone_id)
                if current is None or template.priority > current.priority or (
                    template.priority == current.priority and randomizer.random() > 0.5
                ):
                    selected[zone_id] = template

    return [
        {
            "event_id": template.event_id,
            "zone": template.zone.replace("_", " ").upper() if template.zone else "CITYWIDE",
            "zone_id": zone_id,
            "headline": template.headline,
            "age": "just now",
            "intensity": template.intensity,
            "tone": template.tone,
        }
        for zone_id, template in selected.items()
    ]
