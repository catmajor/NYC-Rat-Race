"""Assemble the (game_zone x 15-min) point-in-time feature store.

Combines:
  * demand:  pickups / dropoffs per 15-min bucket (all 4 TLC services)
  * weather: hourly per-zone readings stamped onto each 15-min bucket
  * events:  daily citywide GDELT features (joined with a 1-day lag)

Every 15-min bucket is present for every zone (missing demand = 0) so that
lag/rolling features computed downstream are exact.

Output: ``feature_store.parquet``
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config

GRID_TZ_STR = "America/New_York"

FEATURE_COLS = [
    "pickups",
    "dropoffs",
    "temp_c",
    "wind_ms",
    "vis_km",
    "precip_mm",
    "precip_3h_mm",
    "event_count",
    "event_mentions",
    "event_articles",
    "avg_tone",
    "avg_goldstein",
]


def load() -> pd.DataFrame:
    pickups = pd.read_parquet(config.ML_ARTIFACTS / "pickups_15m.parquet")
    dropoffs = pd.read_parquet(config.ML_ARTIFACTS / "dropoffs_15m.parquet")
    weather = pd.read_parquet(config.ML_ARTIFACTS / "weather_hourly.parquet")
    events = pd.read_parquet(config.ML_ARTIFACTS / "events_daily.parquet")

    # 1) Full 15-min grid for every zone (demand padded to zero).
    ts = pd.date_range(config.WINDOW_START, config.WINDOW_END, freq="15min", inclusive="left")
    grid = pd.MultiIndex.from_product(
        [sorted(config.GAME_ZONES), ts], names=["game_zone", "ts"]
    ).to_frame(index=False)

    pickups["ts"] = pd.to_datetime(pickups["ts"])
    dropoffs["ts"] = pd.to_datetime(dropoffs["ts"])
    grid = grid.merge(pickups, on=["game_zone", "ts"], how="left")
    grid = grid.merge(dropoffs, on=["game_zone", "ts"], how="left")
    grid["pickups"] = grid["pickups"].fillna(0).astype(int)
    grid["dropoffs"] = grid["dropoffs"].fillna(0).astype(int)

    # 2) Weather: hourly -> stamp onto 15-min buckets (as-of, no lookahead).
    weather["hour"] = pd.to_datetime(weather["hour"])
    weather = weather.sort_values("hour")
    weather["precip_3h_mm"] = (
        weather.groupby("game_zone")["precip_mm"].transform(lambda s: s.rolling(3, min_periods=1).sum())
    )
    grid["hour"] = grid["ts"].dt.floor("h")
    grid = grid.merge(weather, on=["game_zone", "hour"], how="left").drop(columns=["hour"])

    # 3) Events: features dated `date - lag` are known at date (no lookahead).
    ev = events[["date", "event_count", "event_mentions", "event_articles", "avg_tone", "avg_goldstein"]].copy()
    ev["date"] = pd.to_datetime(ev["date"])
    ev["feat_date"] = ev["date"] + pd.Timedelta(days=config.EVENTS_LAG_DAYS)
    grid["feat_date"] = grid["ts"].dt.floor("D")
    grid = grid.merge(
        ev.drop(columns=["date"]), on="feat_date", how="left"
    ).drop(columns=["feat_date"])

    grid["ts"] = pd.to_datetime(grid["ts"])

    # Weather / events fill on a few edge hours (per zone forward fill).
    weather_cols = ["temp_c", "wind_ms", "vis_km", "precip_mm", "precip_3h_mm"]
    for c in weather_cols + ["event_count", "event_mentions", "event_articles", "avg_tone", "avg_goldstein"]:
        grid[c] = grid.groupby("game_zone")[c].ffill()
    grid[weather_cols] = grid[weather_cols].fillna(
        {"temp_c": 0.0, "wind_ms": 0.0, "vis_km": 0.0, "precip_mm": 0.0, "precip_3h_mm": 0.0}
    )
    grid[["event_count", "event_mentions", "event_articles"]] = grid[
        ["event_count", "event_mentions", "event_articles"]
    ].fillna(0)
    grid[["avg_tone", "avg_goldstein"]] = grid[["avg_tone", "avg_goldstein"]].fillna(0.0)

    grid = grid[["game_zone", "ts"] + FEATURE_COLS].reset_index(drop=True)
    grid = grid.sort_values(["game_zone", "ts"]).reset_index(drop=True)
    return grid


def main() -> None:
    store = load()
    out = config.ML_ARTIFACTS / "feature_store.parquet"
    store.to_parquet(out, index=False)
    print(f"[store] wrote {out} ({len(store):,} rows)")
    print(f"[store] zones: {store.game_zone.nunique()} | ts min/max: {store.ts.min()} / {store.ts.max()}")


if __name__ == "__main__":
    main()