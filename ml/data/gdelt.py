"""Aggregate GDELT events into daily citywide features.

GDELT has no intra-day timestamps (only ``SQLDATE``), so event features are
daily. We compute citywide counts / raw-mention intensity / average tone and
Goldstein scale per day. In ``build_train_dataset`` these are joined with a
1-day lag so news that is not yet known at the cutoff is never used.

Output: ``events_daily.parquet`` (date, event_count, event_mentions, avg_tone, avg_goldstein)
"""
from __future__ import annotations

import glob

import polars as pl

from .. import config

NEEDED = ["SQLDATE", "NumMentions", "NumArticles", "AvgTone", "GoldsteinScale"]


def main() -> None:
    files = sorted(
        glob.glob(str(config.GDELT_DIR / "gdelt_events_nyc_*.parquet"))
    )
    if not files:
        raise FileNotFoundError(f"no GDELT parquet under {config.GDELT_DIR}")

    frames = []
    for f in files:
        df = pl.scan_parquet(f).select(NEEDED)
        # Filter to the replay window (SQLDATE is YYYYMMDD int).
        start = int(config.WINDOW_START[:4] + config.WINDOW_START[5:7] + config.WINDOW_START[8:10])
        end = int(config.WINDOW_END[:4] + config.WINDOW_END[5:7] + config.WINDOW_END[8:10])
        df = df.filter(pl.col("SQLDATE").is_between(start, end))
        frames.append(df)

    daily = (
        pl.concat(frames)
        .group_by(pl.col("SQLDATE").alias("date"))
        .agg(
            event_count=pl.len(),
            event_mentions=pl.col("NumMentions").fill_null(0).sum(),
            event_articles=pl.col("NumArticles").fill_null(0).sum(),
            avg_tone=pl.col("AvgTone").mean(),
            avg_goldstein=pl.col("GoldsteinScale").mean(),
        )
        .collect()
        .sort("date")
    )

    # Human-readable date helper for the KIM engine / debugging.
    daily = daily.with_columns(
        pl.col("date").cast(pl.String).str.strptime(pl.Date, "%Y%m%d", strict=False)
    )

    out = config.ML_ARTIFACTS / "events_daily.parquet"
    daily.write_parquet(out)
    print(f"[gdelt] wrote {out} ({len(daily):,} days)")
    print(daily.tail(4).to_pandas().to_string(index=False))


if __name__ == "__main__":
    main()