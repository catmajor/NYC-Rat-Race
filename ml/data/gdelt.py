"""Aggregate GDELT events into daily citywide + per-region features.

GDELT only records a daily timestamp (``SQLDATE``), so features are daily. We
compute:
  * a citywide daily aggregate (event_count / mentions / articles / tone /
    Goldstein scale over all NYC-bounding-box events), and
  * a per-region daily aggregate, assigning every event to one of the 12 game
    zones by point-in-polygon of its ``ActionGeo`` coordinates against the same
    taxi-zone region geometry the rest of the pipeline uses.

Per-region days with no events keep ``event_count = event_mentions = 0`` and
fill tone / Goldstein with the zone's trailing 7-day average (falling back to
the citywide value) so the joined feature store never carries NaNs.

In ``build_train_dataset`` citywide + per-region features are joined with a
1-day lag so news that is not yet known at the cutoff is never used.

Outputs:
  * ``events_daily.parquet``      (date, citywide 5 cols)  -- unchanged schema
  * ``events_daily_zone.parquet`` (date, game_zone, zone_event_count,
    zone_event_mentions, zone_avg_tone, zone_avg_goldstein)
  * ``region_polygons.geojson``   -- per-region WGS84 polygons (+ bbox) for the
    game backend to assign GDELT/news points the same way.
"""
from __future__ import annotations

import glob
import json

import numpy as np
import pandas as pd
import polars as pl
import pyproj
from shapely.geometry import Point, mapping
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .. import config

NEEDED = [
    "SQLDATE",
    "NumMentions",
    "NumArticles",
    "AvgTone",
    "GoldsteinScale",
    "ActionGeo_Lat",
    "ActionGeo_Long",
]

ZONE_COLS = [
    "zone_event_count",
    "zone_event_mentions",
    "zone_avg_tone",
    "zone_avg_goldstein",
]

# Simplification tolerance (degrees lon/lat, ~450 m) for the polygon file the
# game backend consumes. News assignment only needs gazetteer-level precision.
POLY_SIMPLIFY_DEG = 0.004


def _region_polygons_2263() -> dict[str, object]:
    """{game_zone: unioned member polygons} in the zone shapefile CRS (EPSG:2263)."""
    from . import zones as zmod

    zone_map = pd.read_parquet(config.ML_ARTIFACTS / "zone_map.parquet")
    geom = zmod.load_zone_geom()
    return {
        slug: unary_union([g for loc in zone_map.loc[zone_map.game_zone == slug, "loc_id"] if (g := geom.get(loc)) is not None])
        for slug in config.GAME_ZONES
    }


def _load_window() -> pd.DataFrame:
    files = sorted(glob.glob(str(config.GDELT_DIR / "gdelt_events_nyc_*.parquet")))
    if not files:
        raise FileNotFoundError(f"no GDELT parquet under {config.GDELT_DIR}")

    # Filter to the replay window (SQLDATE is YYYYMMDD int).
    start = int(config.WINDOW_START[:4] + config.WINDOW_START[5:7] + config.WINDOW_START[8:10])
    end = int(config.WINDOW_END[:4] + config.WINDOW_END[5:7] + config.WINDOW_END[8:10])

    frames = []
    for f in files:
        df = pl.scan_parquet(f).select(NEEDED)
        df = df.filter(pl.col("SQLDATE").is_between(start, end))
        frames.append(df)
    if not frames:
        raise RuntimeError(f"no GDELT events in window {config.WINDOW_START}..{config.WINDOW_END}")
    return pl.concat(frames).collect().to_pandas()


def _assign_zones(df: pd.DataFrame) -> np.ndarray:
    """Per-event game-zone slug (None when not inside any region). EPSG:2263 match."""
    polys = _region_polygons_2263()
    slugs = list(polys)
    # Empty zones (no member geometry) must not break the index.
    empty = [s for s in slugs if polys[s].is_empty]
    if empty:
        print(f"[gdelt] [warn] no geometry for regions: {empty}", flush=True)
    valid = [(s, g) for s, g in polys.items() if not g.is_empty]
    slugs_v = [s for s, _ in valid]
    tree = STRtree([g for _, g in valid])

    lat = df["ActionGeo_Lat"].to_numpy(dtype=float)
    lon = df["ActionGeo_Long"].to_numpy(dtype=float)
    ok = ~(np.isnan(lat) | np.isnan(lon))
    if not ok.any():
        return np.full(len(df), None, dtype=object)

    transformer = pyproj.Transformer.from_crs("EPSG:4326", config.ZONES_SHP_EPSG, always_xy=True)
    x, y = transformer.transform(lon[ok], lat[ok])
    points = np.array([Point(float(xi), float(yi)) for xi, yi in zip(x, y)], dtype=object)

    out = np.full(len(df), None, dtype=object)
    # Shapely returns shape (2, n): row 0 = input index, row 1 = tree index.
    res = tree.query(points, predicate="intersects")
    if res.shape[1]:
        in_idx, tree_idx = res[0], res[1]
        out[in_idx] = np.array(slugs_v)[tree_idx]
    return out


def _citywide_daily(df: pd.DataFrame) -> pd.DataFrame:
    daily = (
        df.assign(date=df["SQLDATE"].astype("int64"))
        .groupby("date", as_index=False)
        .agg(
            event_count=("SQLDATE", "size"),
            event_mentions=("NumMentions", "sum"),
            event_articles=("NumArticles", "sum"),
            avg_tone=("AvgTone", "mean"),
            avg_goldstein=("GoldsteinScale", "mean"),
        )
    )
    daily["date"] = pd.to_datetime(daily["date"].astype(str), format="%Y%m%d")
    return daily.sort_values("date").reset_index(drop=True)


def _zone_daily(df: pd.DataFrame, citywide: pd.DataFrame) -> pd.DataFrame:
    """Complete (game_zone x date) table with sparse zone-days padded."""
    df = df.assign(
        date=pd.to_datetime(df["SQLDATE"].astype("int64").astype(str), format="%Y%m%d"),
        zone=_assign_zones(df),
    ).dropna(subset=["zone"])
    per = (
        df.groupby(["date", "zone"], as_index=False)
        .agg(
            event_count=("SQLDATE", "size"),
            event_mentions=("NumMentions", "sum"),
            avg_tone=("AvgTone", "mean"),
            avg_goldstein=("GoldsteinScale", "mean"),
        )
        .rename(columns={"zone": "game_zone"})
    )
    per["date"] = pd.to_datetime(per["date"])

    # Full zone x day grid for the replay window.
    days = pd.date_range(config.WINDOW_START, config.WINDOW_END, freq="D")
    grid = pd.MultiIndex.from_product(
        [sorted(config.GAME_ZONES), days], names=["game_zone", "date"]
    ).to_frame(index=False)
    per = grid.merge(per, on=["game_zone", "date"], how="left").sort_values(
        ["game_zone", "date"]
    )

    per["zone_event_count"] = per["event_count"].fillna(0).astype(int)
    per["zone_event_mentions"] = per["event_mentions"].fillna(0).astype(int)

    # Tone / Goldstein fallback: trailing 7-day zone average over the sparse
    # zone-days (min 3 observed), else the day's citywide value; so the joined
    # feature store never carries NaN.
    cw = citywide.set_index("date")[["avg_tone", "avg_goldstein"]]
    for src, dst in (("avg_tone", "zone_avg_tone"), ("avg_goldstein", "zone_avg_goldstein")):
        rolled = per.groupby("game_zone")[src].transform(
            lambda s: s.rolling(7, min_periods=3).mean()
        )
        per[dst] = rolled.where(rolled.notna(), per["date"].map(cw[src])).fillna(0.0)

    return per[["date", "game_zone", *ZONE_COLS]].reset_index(drop=True)


def build_event_tables() -> None:
    df = _load_window()
    citywide = _citywide_daily(df)
    zone = _zone_daily(df, citywide)

    out_cw = config.ML_ARTIFACTS / "events_daily.parquet"
    out_zone = config.ML_ARTIFACTS / "events_daily_zone.parquet"
    citywide.to_parquet(out_cw, index=False)
    zone.to_parquet(out_zone, index=False)

    print(f"[gdelt] wrote {out_cw} ({len(citywide):,} days) and {out_zone} ({len(zone):,} rows)")
    print(
        "zone-day coverage: "
        + ", ".join(
            f"{gz} {n:.0%}"
            for gz, n in zone.assign(d=zone.zone_event_count > 0).groupby("game_zone")["d"].mean().sort_values(ascending=False).items()
        )
    )


def write_region_polygons() -> None:
    """WGS84 region unions (+ bbox) for the backend, simplified for ray-casting."""
    out = config.MODELS_DIR / "region_polygons.geojson"
    polys = _region_polygons_2263()
    transformer = pyproj.Transformer.from_crs(config.ZONES_SHP_EPSG, "EPSG:4326", always_xy=True)

    features = []
    for slug in config.GAME_ZONES:
        g = polys[slug]
        if g.is_empty:
            print(f"[gdelt] [warn] no geometry for {slug}; skipping polygon")
            continue
        from shapely.ops import transform as shp_transform

        wgs = shp_transform(transformer.transform, g)
        simplified = wgs.simplify(POLY_SIMPLIFY_DEG, preserve_topology=True)
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "name": slug,
                    "bbox": [round(v, 5) for v in simplified.bounds],
                },
                "geometry": mapping(simplified),
            }
        )

    json.dump(
        {"type": "FeatureCollection", "features": features},
        open(out, "w", encoding="utf-8"),
        separators=(",", ":"),
    )
    print(f"[gdelt] wrote {out} ({len(features)} region polygons)")


def main() -> None:
    build_event_tables()
    write_region_polygons()
    print(pl.read_parquet(config.ML_ARTIFACTS / "events_daily_zone.parquet").head(5).to_pandas().to_string(index=False))


if __name__ == "__main__":
    main()