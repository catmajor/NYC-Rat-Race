from datetime import datetime

from app.data import InMemoryAnalogueStore
from app.models import HistoricalEpisode, HistoricalState, ZONE_IDS


def _episode(timestamp: datetime, value: float) -> HistoricalEpisode:
    return HistoricalEpisode(
        state=HistoricalState(
            timestamp=timestamp,
            demand_by_zone={zone_id: value for zone_id in ZONE_IDS},
        ),
        next_demand_by_zone={},
    )


def test_weekday_mean_is_region_scoped_and_falls_back_when_sparse() -> None:
    store = InMemoryAnalogueStore(
        [
            _episode(datetime(2024, 10, 18, 8), 10.0),
            _episode(datetime(2024, 10, 25, 8), 30.0),
            _episode(datetime(2024, 10, 19, 8), 50.0),
        ]
    )

    friday = store.weekday_mean(datetime(2024, 11, 1, 8))
    assert friday["midtown"] == 20.0

    sparse = store.weekday_mean(datetime(2024, 10, 20, 8), min_samples=2)
    assert sparse["midtown"] == 30.0
