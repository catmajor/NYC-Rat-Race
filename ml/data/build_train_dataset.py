"""Build the point-in-time training rows for the demand model.

For every business turn (08:00 / 11:00 / 14:00 / 17:00) and game zone we create
three rows -- one per hourly forecast bucket (horizon 1/2/3 hours ahead). All
features are computed strictly from information available at the cutoff:

  * time of day / calendar
  * demand history: live 15-min lags / rolling / trail / neighbour zones
    (used by the ``live`` variant; the game model ignores demand entirely)
  * weather at the cutoff (`precip_3h` = rain over the previous 3 h)
  * GDELT event features from the previous day (no same-day lookahead)
  * `horizon` and numeric `zone_id` (categoricals don't survive ONNX) 

Target is the pickups counted in that single future hour bucket.

Output: ``train_dataset.parquet`` -- a full superset of every variant's columns,
split into train / validation / holdout. Variants simply select their column
subset at training time.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config

TARGET_COLS = ["target_h1", "target_h2", "target_h3"]


def load_store() -> pd.DataFrame:
    return pd.read_parquet(config.ML_ARTIFACTS / "feature_store.parquet")


def zone_features(zdf: pd.DataFrame) -> pd.DataFrame:
    zdf = zdf.sort_values("ts").reset_index(drop=True)
    s = zdf["pickups"].astype(float)
    out = pd.DataFrame(index=zdf.index)
    for k in range(1, 9):
        out[f"lag_{15*k}m"] = s.shift(k)
    out["roll_1h"] = s.shift(1).rolling(4).mean()
    out["roll_4h"] = s.shift(1).rolling(16).mean()
    out["trail_3h"] = s.shift(1).rolling(12).sum()
    out["trail_prev_3h"] = s.shift(13).rolling(12).sum()
    out["trail_1h"] = s.shift(1).rolling(4).sum()
    out["trail_3h_feat"] = s.shift(1).rolling(12).sum()
    # Targets: pickups in the 4-bucket hour starting (h-1) hours after cutoff.
    out["target_h1"] = s.rolling(4).sum().shift(-3)
    out["target_h2"] = s.rolling(4).sum().shift(-7)
    out["target_h3"] = s.rolling(4).sum().shift(-11)
    return out


def neighbour_demand(store: pd.DataFrame) -> pd.DataFrame:
    """Wide per-zone trailing sums, then average over the neighbour graph."""
    neighbors = pd.read_parquet(config.ML_ARTIFACTS / "zone_neighbors.parquet")
    zones = sorted(config.GAME_ZONES)
    piv1, piv3 = {}, {}
    for gz in zones:
        zdf = store[store.game_zone == gz].sort_values("ts").reset_index(drop=True)
        s = zdf["pickups"].astype(float)
        piv1[gz] = {"ts": zdf["ts"], "v": s.shift(1).rolling(4).sum().to_numpy()}
        piv3[gz] = {"ts": zdf["ts"], "v": s.shift(1).rolling(12).sum().to_numpy()}

    wide1 = pd.DataFrame({gz: piv1[gz]["v"] for gz in zones})
    wide3 = pd.DataFrame({gz: piv3[gz]["v"] for gz in zones})
    ts = piv1[zones[0]]["ts"].to_numpy()

    rows = []
    for gz in zones:
        nb = neighbors[neighbors.game_zone == gz]["neighbor"].tolist()
        rows.append(
            {
                "game_zone": gz,
                "neighbor_demand_1h": wide1[nb].mean(axis=1).to_numpy(),
                "neighbor_demand_3h": wide3[nb].mean(axis=1).to_numpy(),
            }
        )

    out = pd.DataFrame(
        {
            "game_zone": np.concatenate([[r["game_zone"]] * len(ts) for r in rows]),
            "ts": np.concatenate([ts] * len(rows)),
            "neighbor_demand_1h": np.concatenate([r["neighbor_demand_1h"] for r in rows]),
            "neighbor_demand_3h": np.concatenate([r["neighbor_demand_3h"] for r in rows]),
        }
    )
    return out


def build() -> pd.DataFrame:
    store = load_store()

    parts = []
    for gz, zdf in store.groupby("game_zone"):
        zf = zone_features(zdf)
        row = pd.concat([zdf.reset_index(drop=True), zf], axis=1)
        parts.append(row)
    store_feat = pd.concat(parts, ignore_index=True)

    store_feat = store_feat.merge(neighbour_demand(store), on=["game_zone", "ts"], how="left")

    cut = store_feat[
        (store_feat.ts.dt.minute == 0) & (store_feat.ts.dt.hour.isin(config.TURN_HOURS))
    ].copy()
    cut["hour"] = cut.ts.dt.hour
    cut["dow"] = cut.ts.dt.dayofweek
    cut["month"] = cut.ts.dt.month
    cut["dayofyear"] = cut.ts.dt.dayofyear
    cut["zone_id"] = cut.game_zone.astype("category").cat.codes
    cut["cutoff"] = cut.ts

    rows = []
    for h in config.HORIZON_HOURS:
        t = cut.copy()
        t["horizon"] = h
        t["target"] = t[f"target_h{h}"]
        rows.append(t.drop(columns=TARGET_COLS))
    ds = pd.concat(rows, ignore_index=True)

    ds["split"] = (
        np.select(
            [
                ds.cutoff < pd.Timestamp(config.TRAIN_CUTOFF),
                ds.cutoff < pd.Timestamp(config.VAL_CUTOFF),
            ],
            ["train", "validation"],
            default="holdout",
        )
        .astype(str)
    )

    # Keep every feature column so the dataset is a superset usable by all
    # variants; each variant picks its subset via its FEATURE_ORDER.
    ds = ds.dropna()
    ds = ds.sort_values(["game_zone", "cutoff", "horizon"]).reset_index(drop=True)
    return ds


def main() -> None:
    ds = build()
    out = config.train_dataset_path()
    ds.to_parquet(out, index=False)
    print(f"[dataset] wrote {out} ({len(ds):,} rows)")
    print(ds.groupby("split").size().to_string())
    print(ds.groupby("horizon").target.describe().to_string())


if __name__ == "__main__":
    main()