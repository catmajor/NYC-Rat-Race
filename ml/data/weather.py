"""Decode NOAA ISD hourly observations into per-game-zone weather features.

ISD files are CSVs with composite fields (``TMP=+0067,5``, ``WND=140,5,N,0021,5``,
``VIS=004023,5,N,5``, ``AA1=01,0010,3,1``). We decode temperature (C), wind speed
(m/s), visibility (km) and precipitation (mm) per observation, aggregate to
hourly per station, then assign each game zone the inverse-distance weighted
value of its nearest stations. Gaps are forward-filled (bounded) and finally
filled with a citywide median.

Output: ``weather_hourly.parquet``  (hour, game_zone, temp_c, wind_ms, vis_km, precip_mm)
"""
from __future__ import annotations

import glob

import numpy as np
import pandas as pd

from .. import config

AA_COLS = [f"AA{i}" for i in range(1, 7)]


def _dec(value, scale: int, missing: set[int]) -> float:
    """Decode an ISD scalar field like '+0067,5' (returns value/scale)."""
    if not value:
        return np.nan
    head = str(value).split(",", 1)[0].strip()
    if not head:
        return np.nan
    try:
        v = int(head)
    except (ValueError, TypeError):
        return np.nan
    if v in missing:
        return np.nan
    return v / scale


def _wind_ms(value) -> float:
    if not value:
        return np.nan
    parts = str(value).split(",")
    if len(parts) < 5:
        return np.nan
    return _dec(parts[3], 10, {9999, 999})


def _precip_mm(value) -> float:
    """Decode an AA-field '01,0010,3,1' -> liquid precipitation depth in mm."""
    if not value:
        return 0.0
    parts = str(value).split(",")
    if len(parts) < 2:
        return 0.0
    try:
        depth = int(parts[1])
    except ValueError:
        return 0.0
    if depth in (9999, 999):
        return 0.0
    return depth * 0.254  # hundredths of an inch -> mm


def decode_station(file: str) -> pd.DataFrame:
    """Decode one station-year CSV into hourly per-station aggregates."""
    cols = ["STATION", "DATE", "LATITUDE", "LONGITUDE", "NAME"] + ["TMP", "WND", "VIS"] + AA_COLS
    raw = pd.read_csv(file, usecols=lambda c: c in cols, dtype=str)
    raw = raw.reindex(columns=cols)
    raw = raw.dropna(subset=["DATE"]).copy()

    hour = pd.to_datetime(raw["DATE"], errors="coerce").dt.floor("h")
    raw = raw.assign(hour=hour).dropna(subset=["hour"])

    raw["temp_c"] = raw["TMP"].map(lambda v: _dec(v, 10, {9999, 999}))
    raw["wind_ms"] = raw["WND"].map(_wind_ms)
    raw["vis_km"] = raw["VIS"].map(lambda v: _dec(v, 1000, {999999, 99999}))
    raw["precip_mm"] = raw[AA_COLS].map(_precip_mm).sum(axis=1).fillna(0.0)

    valid = pd.DataFrame(
        {
            "station": raw["STATION"],
            "hour": raw["hour"],
            "lat": pd.to_numeric(raw["LATITUDE"], errors="coerce"),
            "lon": pd.to_numeric(raw["LONGITUDE"], errors="coerce"),
            "name": raw["NAME"],
        }
    )
    valid[["temp_c", "wind_ms", "vis_km", "precip_mm"]] = raw[
        ["temp_c", "wind_ms", "vis_km", "precip_mm"]
    ]

    agg = (
        valid.groupby(["station", "hour"], as_index=False)
        .agg(
            lat=("lat", "first"),
            lon=("lon", "first"),
            name=("name", "first"),
            temp_c=("temp_c", "mean"),
            wind_ms=("wind_ms", "mean"),
            vis_km=("vis_km", "mean"),
            precip_mm=("precip_mm", "sum"),
        )
        .dropna(subset=["temp_c", "wind_ms", "vis_km"], how="all")
    )
    return agg


def station_locs(station_hourly: pd.DataFrame) -> list[dict]:
    out = []
    for station, g in station_hourly.groupby("station"):
        row = g[g.lat.notna()].iloc[0] if g.lat.notna().any() else g.iloc[0]
        out.append({"station": station, "lat": float(row.lat), "lon": float(row.lon), "name": row.name})
    return out


def _hav_km(a_lat: float, a_lon: float, b_lat, b_lon) -> np.ndarray:
    from numpy import arcsin, cos, radians, sin, sqrt

    a_lat, a_lon = radians(a_lat), radians(a_lon)
    b_lat, b_lon = radians(np.asarray(b_lat, dtype=float)), radians(np.asarray(b_lon, dtype=float))
    dlat = b_lat - a_lat
    dlon = b_lon - a_lon
    x = sin(dlat / 2) ** 2 + cos(a_lat) * cos(b_lat) * sin(dlon / 2) ** 2
    return 6371.0 * 2 * arcsin(sqrt(x))


def resolve_zones(station_hourly: pd.DataFrame, game_zones: pd.DataFrame, locs: list[dict]) -> pd.DataFrame:
    """Inverse-distance weighted weather for every game zone and hour."""
    all_hours = pd.date_range(config.WINDOW_START, config.WINDOW_END, freq="h")
    chunks = []
    for _, zone in game_zones.iterrows():
        gz = zone["game_zone"]
        dists = sorted(
            (
                (
                    s["station"],
                    float(_hav_km(zone["centroid_lat"], zone["centroid_lon"], s["lat"], s["lon"])),
                )
                for s in locs
            ),
            key=lambda t: t[1],
        )[: config.WEATHER_NEAREST_K]

        weights = {st: 1.0 / max(d, 0.5) for st, d in dists}
        sub = station_hourly[station_hourly.station.isin(weights)].copy()
        sub = sub.assign(w=sub.station.map(weights).fillna(0.0))

        def zonify(g) -> pd.Series:
            w = g["w"]
            return pd.Series(
                {
                    "temp_c": np.average(g.temp_c, weights=w) if g.temp_c.notna().any() else np.nan,
                    "wind_ms": np.average(g.wind_ms, weights=w) if g.wind_ms.notna().any() else np.nan,
                    "vis_km": np.average(g.vis_km, weights=w) if g.vis_km.notna().any() else np.nan,
                    "precip_mm": g.precip_mm.sum() if g.precip_mm.notna().any() else np.nan,
                }
            )

        frame = sub.groupby("hour", sort=False).apply(zonify).reindex(all_hours)
        frame = frame.ffill(limit=config.WEATHER_FILL_MAX_HOURS).bfill()
        frame = frame.fillna({"temp_c": 0.0, "wind_ms": 0.0, "vis_km": 0.0, "precip_mm": 0.0})
        frame = frame.reset_index().rename(columns={"index": "hour"})
        frame["game_zone"] = gz
        chunks.append(frame)
    return pd.concat(chunks, ignore_index=True)


def main() -> None:
    files = sorted(glob.glob(str(config.NOAA_DIR / "*_2018.csv"))) + sorted(
        glob.glob(str(config.NOAA_DIR / "*_2019.csv"))
    )
    if not files:
        raise FileNotFoundError(f"no NOAA ISD files under {config.NOAA_DIR}")

    frames = [decode_station(f) for f in files]
    station_hourly = pd.concat(frames, ignore_index=True)
    locs = station_locs(station_hourly)
    print(f"[weather] decoded {len(files)} files, {len(locs)} stations", flush=True)

    game_zones = pd.read_parquet(config.ML_ARTIFACTS / "game_zones.parquet")
    resolved = resolve_zones(station_hourly, game_zones, locs)

    out = config.ML_ARTIFACTS / "weather_hourly.parquet"
    resolved.to_parquet(out, index=False)
    print(f"[weather] wrote {out} ({len(resolved):,} rows)")
    sample = resolved[(resolved.game_zone == "midtown") & (resolved.hour < "2019-10-20")]
    print(sample.head(8).to_string(index=False))


if __name__ == "__main__":
    main()