"""Central configuration for the Rat Race ML pipeline.

Paths are resolved relative to the repository root (parent of ``ml/``).
"""
from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[1]
ML_DIR = Path(__file__).resolve().parent
DATA_DIR = REPO_ROOT / "data"
TLC_DIR = DATA_DIR / "tlc_nyc"
NOAA_DIR = DATA_DIR / "noaa_isd_nyc"
GDELT_DIR = DATA_DIR / "gdelt_nyc" / "events"

ML_ARTIFACTS = ML_DIR / "data"          # caches + derived feature tables
MODELS_DIR = ML_DIR / "models"          # trained models + ONNX outputs
WEB_DIR = REPO_ROOT / "web"             # optional: share artifacts with backend

ML_ARTIFACTS.mkdir(parents=True, exist_ok=True)
MODELS_DIR.mkdir(parents=True, exist_ok=True)

# Active gameplay variant (see the Model section below): determines which
# feature order is shipped to the backend.
VARIANT = "delayed"


def train_dataset_path(variant: str = VARIANT) -> Path:
    """Feature dataset for a pipeline variant (kept separate so variants can coexist)."""
    return ML_ARTIFACTS / f"train_dataset_{variant}.parquet"


def model_report_path(variant: str = VARIANT) -> Path:
    return MODELS_DIR / f"model_report_{variant}.json"


def feature_columns_path(variant: str = VARIANT) -> Path:
    return MODELS_DIR / f"feature_columns_{variant}.json"


def model_stem(variant: str = VARIANT) -> str:
    """File stem for a variant's trained models (e.g. ``demand_delayed``)."""
    return f"demand_{variant}"

# External reference data (TLC zone lookup + geometry). Cached offline.
LOOKUP_CSV_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zone_lookup.csv"
ZONES_SHP_ZIP_URL = "https://d37ci6vzurychx.cloudfront.net/misc/taxi_zones.zip"

LOOKUP_CSV = ML_ARTIFACTS / "taxi_zone_lookup.csv"
ZONES_SHP_ZIP = ML_ARTIFACTS / "taxi_zones.zip"
# EPSG of the taxi_zones shapefile (NAD83 / New York Long Island, US ft).
ZONES_SHP_EPSG = "EPSG:2263"

# ---------------------------------------------------------------------------
# Game structure
# ---------------------------------------------------------------------------
# 3 days x 4 turns/day at these wall-clock hours; working window 08:00-20:00.
TURN_HOURS = [8, 11, 14, 17]
# Hourly forecast buckets inside a turn, relative to the cutoff.
HORIZON_HOURS = [1, 2, 3]

# Game zones. slug -> (display name, priority for neighborhood assignment).
GAME_ZONES: dict[str, str] = {
    "harlem": "Harlem",
    "upper_west": "Upper West Side",
    "upper_east": "Upper East Side",
    "midtown": "Midtown",
    "downtown": "Downtown",
    "north_brooklyn": "North Brooklyn",
    "south_brooklyn": "South Brooklyn",
    "queens_west": "Queens West",
    "airports": "Airports",
}

# Historical replay window used to build the feature store / train the model.
WINDOW_START = "2018-01-01"
WINDOW_END = "2020-01-01"

# Point-in-time split (by cutoff date; no leakage across splits).
TRAIN_CUTOFF = "2019-09-01"   # rows strictly before this -> train
VAL_CUTOFF = "2019-10-01"     # rows in [TRAIN_CUTOFF, VAL_CUTOFF) -> validation
# rows >= VAL_CUTOFF -> holdout (covers the 2019-10-18 scenario)

# ---------------------------------------------------------------------------
# Feature pipeline (aggregation buckets)
# ---------------------------------------------------------------------------
AGG_INTERVAL_MIN = 15     # feature-store resolution
OD_INTERVAL_HOUR = 1      # origin-destination flow resolution

# ---------------------------------------------------------------------------
# Weather
# ---------------------------------------------------------------------------
# Stations with no reading near a zone fall back to a citywide median instead.
WEATHER_NEAREST_K = 3
WEATHER_FILL_MAX_HOURS = 24  # forward-fill limit when a station is offline

# ---------------------------------------------------------------------------
# GDELT
# ---------------------------------------------------------------------------
# Event features are joined with a 1-day lag so that only news that is already
# known at the cutoff time is used (point-in-time safe).
EVENTS_LAG_DAYS = 1

# ---------------------------------------------------------------------------
# Model feature variants
# ---------------------------------------------------------------------------
# Two feature pipelines are kept in the codebase:
#
# * ``live``   -- full 29-feature model using live 15-min demand snapshots
#   (lags back to 2 h, trailing windows, rolling means, neighbour zones).
#   Used for offline experiments and back-casting only.
#
# * ``delayed`` -- the game model (``VARIANT``). Demand is NOT known live:
#   only day-old information (same-hour yesterday, same hour last week, and
#   the trailing 24 h total) plus calendar, weather and events. Players bid
#   without a live taxi feed.
#
# ``FEATURE_ORDER`` is the active variant's columns, persisted to
# models/feature_columns.json and shared with the backend so ONNX input rows
# can be built in exactly the right order.
FEATURE_ORDER_LIVE: list[str] = [
    # time
    "hour",
    "dow",
    "month",
    "dayofyear",
    # recent demand lags (15-min)
    "lag_15m",
    "lag_30m",
    "lag_45m",
    "lag_60m",
    "lag_75m",
    "lag_90m",
    "lag_105m",
    "lag_120m",
    # trailing windows / momentum
    "roll_1h",
    "roll_4h",
    "trail_3h",
    "trail_prev_3h",
    # cross-zone signal
    "neighbor_demand_1h",
    "neighbor_demand_3h",
    # weather at the cutoff
    "temp_c",
    "wind_ms",
    "vis_km",
    "precip_mm",
    "precip_3h_mm",
    # events (lagged one day, citywide)
    "event_count",
    "event_mentions",
    "avg_tone",
    "avg_goldstein",
    # multi-horizon + zone identity
    "horizon",
    "zone_id",
]

FEATURE_ORDER_DELAYED: list[str] = [
    # time
    "hour",
    "dow",
    "month",
    "dayofyear",
    # day-old demand (no live taxi feed)
    "same_hour_yday",
    "same_hour_prevwk",
    "today_total",
    # weather at the cutoff
    "temp_c",
    "wind_ms",
    "vis_km",
    "precip_mm",
    "precip_3h_mm",
    # events (lagged one day, citywide)
    "event_count",
    "event_mentions",
    "avg_tone",
    "avg_goldstein",
    # multi-horizon + zone identity
    "horizon",
    "zone_id",
]

VARIANTS: dict[str, list[str]] = {
    "live": FEATURE_ORDER_LIVE,
    "delayed": FEATURE_ORDER_DELAYED,
}

FEATURE_ORDER: list[str] = VARIANTS[VARIANT]

# LightGBM hyperparameters shared by all four models.
LGB_PARAMS: dict = {
    "objective": "regression",   # overridden for quantile variants
    "n_estimators": 800,
    "learning_rate": 0.05,
    "num_leaves": 63,
    "min_child_samples": 30,
    "subsample": 0.8,
    "subsample_freq": 1,
    "colsample_bytree": 0.8,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "random_state": 42,
    "n_jobs": -1,
    "verbose": -1,
}

QUANTILE_ALPHAS: dict[str, float] = {"p10": 0.1, "p50": 0.5, "p90": 0.9}


def dump_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, default=str, sort_keys=True), encoding="utf-8")