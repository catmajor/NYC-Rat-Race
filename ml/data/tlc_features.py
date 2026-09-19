"""Aggregate the 4 TLC trip services into (game_zone x 15-min) demand tables.

Reads every monthly TLC parquet in the configured window with DuckDB
(pushdown of required columns + bucketing happens inside DuckDB), maps each
trip's pickup/dropoff LocationID onto the 9 game zones, and writes:

  * ``pickups_15m.parquet``  (game_zone, ts, pickups)      -- every 15-min bucket
  * ``dropoffs_15m.parquet`` (game_zone, ts, dropoffs)
  * ``od_1h.parquet``        (ts, pu_zone, do_zone, trips) -- origin/destination
"""
from __future__ import annotations

import glob
from dataclasses import dataclass

import duckdb
import pandas as pd

from .. import config
from . import zones as zmod


@dataclass(frozen=True)
class Service:
    name: str
    pickup_ts: str                      # demand-time column (uses request time for fhvhv)
    dropoff_ts: str
    pu_col: str
    do_col: str


SERVICES: list[Service] = [
    Service("yellow", "tpep_pickup_datetime", "tpep_dropoff_datetime", "PULocationID", "DOLocationID"),
    Service("green", "lpep_pickup_datetime", "lpep_dropoff_datetime", "PULocationID", "DOLocationID"),
    Service("fhv", "pickup_datetime", "dropOff_datetime", "PUlocationID", "DOlocationID"),
    Service(
        "fhvhv",
        "COALESCE(request_datetime, pickup_datetime)",
        "dropoff_datetime",
        "PULocationID",
        "DOLocationID",
    ),
]


def service_files(service: str) -> list[str]:
    yr = range(int(config.WINDOW_START[:4]), int(config.WINDOW_END[:4]))
    paths = []
    for y in yr:
        paths += sorted(glob.glob(str(config.TLC_DIR / service / f"{service}_tripdata_{y}-*.parquet")))
    if not paths:
        raise FileNotFoundError(f"no {service} parquet files in window")
    return paths


def trips_view(svc: Service) -> str:
    return f"""
    SELECT
        CAST({svc.pu_col} AS BIGINT) AS pu,
        CAST({svc.do_col} AS BIGINT) AS do,
        CAST({svc.pickup_ts} AS TIMESTAMP) AS pu_ts,
        CAST({svc.dropoff_ts} AS TIMESTAMP) AS do_ts
    FROM read_parquet({svc_files(svc.name)!r})
    WHERE {svc.pickup_ts} >= TIMESTAMP '{config.WINDOW_START}'
      AND {svc.pickup_ts} <  TIMESTAMP '{config.WINDOW_END}'
      AND {svc.pickup_ts} IS NOT NULL
      AND {svc.pu_col} IS NOT NULL AND {svc.do_col} IS NOT NULL
    """


def svc_files(service: str, cache: dict = {}) -> list[str]:
    if service in cache:
        return cache[service]
    cache[service] = service_files(service)
    return cache[service]


def run_sql(con: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    return con.execute(sql).df()


def aggregate_service(con: duckdb.DuckDBPyConnection, svc: Service) -> dict[str, pd.DataFrame]:
    trips = trips_view(svc)

    pickups = run_sql(
        con,
        f"""
        SELECT time_bucket(INTERVAL '{config.AGG_INTERVAL_MIN} minutes', pu_ts) AS ts,
               m.game_zone AS game_zone, count(*) AS pickups
        FROM ({trips}) t JOIN zone_map m ON t.pu = m.loc_id
        WHERE m.game_zone IS NOT NULL
        GROUP BY 1, 2
        """,
    )

    dropoffs = run_sql(
        con,
        f"""
        SELECT time_bucket(INTERVAL '{config.AGG_INTERVAL_MIN} minutes', do_ts) AS ts,
               m.game_zone AS game_zone, count(*) AS dropoffs
        FROM ({trips}) t JOIN zone_map m ON t.do = m.loc_id
        WHERE m.game_zone IS NOT NULL
        GROUP BY 1, 2
        """,
    )

    od = run_sql(
        con,
        f"""
        SELECT time_bucket(INTERVAL '{config.OD_INTERVAL_HOUR} hour', pu_ts) AS ts,
               mp.game_zone AS pu_zone, md.game_zone AS do_zone, count(*) AS trips
        FROM ({trips}) t
        JOIN zone_map mp ON t.pu = mp.loc_id
        JOIN zone_map md ON t.do = md.loc_id
        WHERE mp.game_zone IS NOT NULL AND md.game_zone IS NOT NULL
        GROUP BY 1, 2, 3
        """,
    )
    return {"pickups": pickups, "dropoffs": dropoffs, "od": od}


def main() -> None:
    lookup = zmod.load_lookup()
    zone_map = zmod.build_zone_map(lookup)

    con = duckdb.connect()
    con.register("zone_map", zone_map[["loc_id", "game_zone"]])

    parts = {k: [] for k in ("pickups", "dropoffs", "od")}
    for svc in SERVICES:
        print(f"[tlc] aggregating {svc.name} ...", flush=True)
        out = aggregate_service(con, svc)
        for k in parts:
            parts[k].append(out[k])
        print(f"[tlc]   {svc.name} done", flush=True)

    pickups = pd.concat(parts["pickups"]).groupby(["ts", "game_zone"], as_index=False)["pickups"].sum()
    dropoffs = pd.concat(parts["dropoffs"]).groupby(["ts", "game_zone"], as_index=False)["dropoffs"].sum()
    od = pd.concat(parts["od"]).groupby(["ts", "pu_zone", "do_zone"], as_index=False)["trips"].sum()

    for df, name in ((pickups, "pickups_15m"), (dropoffs, "dropoffs_15m"), (od, "od_1h")):
        out_path = config.ML_ARTIFACTS / f"{name}.parquet"
        df.to_parquet(out_path, index=False)
        print(f"[tlc] wrote {out_path} ({len(df):,} rows)")

    p = pickups
    print(
        "\n[tlc] sanity: total pickups in window =",
        f"{p.pickups.sum():,}", "| zones:",
        len(p.game_zone.unique()), "| ts range:", p.ts.min(), "->", p.ts.max(),
    )


if __name__ == "__main__":
    main()