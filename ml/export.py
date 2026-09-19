"""Export trained LightGBM models to ONNX and verify parity with the native model.

Each ``demand_*.joblib`` (sklearn wrapper with trained booster) is converted to a
single-float-input ONNX graph ``demand_*.onnx`` via onnxmltools. Conversion is
verified against the native LightGBM predictions on holdout rows.

NOTE on precision: LightGBM predicts in float64 while
``ai.onnx.ml.TreeEnsembleRegressor`` compares float32 inputs against float32
thresholds, so a feature value that falls within one float32 ulp of a split
threshold can route to the sibling leaf (~1.8% relative worst case on a few
rows per 10k; median deviation ~1e-3). This is inherent to the fp32 ONNX op and
is negligible relative to the models' own holdout MAE (~250 pickups). The
parity check asserts max relative deviation < 3%.

Also emits, for the backend:
  * ``feature_columns.json``  -- feature name order matching the ONNX input
  * ``zone_codes.json``       -- game_zone <-> numeric zone_id used in inputs
  * ``game_zones.json``       -- zone slugs, display names, centroids
"""
from __future__ import annotations

import json

import joblib
import numpy as np
import onnxruntime as ort
import pandas as pd
from skl2onnx.common.data_types import FloatTensorType

from . import config
from .config import dump_json
from .data.build_train_dataset import load_store  # noqa: F401

ONNX_OPSET = 15


def features_for_check(n_rows: int = 200_000, seed: int = 7) -> tuple[np.ndarray, np.ndarray]:
    ds = pd.read_parquet(config.ML_ARTIFACTS / "train_dataset.parquet")
    ds = ds[ds.split == "holdout"]
    if len(ds) > n_rows:
        ds = ds.sample(n_rows, random_state=seed)
    X = ds[config.FEATURE_ORDER].to_numpy(dtype=np.float32)
    y = ds["target"].to_numpy(dtype=np.float64)
    return X, y


def convert(name: str) -> None:
    from onnxmltools import convert_lightgbm

    model = joblib.load(config.MODELS_DIR / f"demand_{name}.joblib")
    onx = convert_lightgbm(
        model,
        initial_types=[("input", FloatTensorType([None, len(config.FEATURE_ORDER)]))],
        target_opset=ONNX_OPSET,
    )
    out = config.MODELS_DIR / f"demand_{name}.onnx"
    out.write_bytes(onx.SerializeToString())
    return out


def verify(name: str, X: np.ndarray, y: np.ndarray) -> float:
    onx_path = config.MODELS_DIR / f"demand_{name}.onnx"
    sess = ort.InferenceSession(str(onx_path), providers=["CPUExecutionProvider"])
    native = joblib.load(config.MODELS_DIR / f"demand_{name}.joblib").predict(X)
    onx_pred = sess.run(None, {"input": X})[0].ravel()
    diff = np.abs(onx_pred - native)
    mae = float(np.mean(np.abs(onx_pred - y)))
    rel = diff / (np.abs(native) + 1.0)
    print(
        f"[export] {name}: max|diff| = {diff.max():.3e} "
        f"({rel.max() * 100:.2f}% rel) | rows > 1.0 = {(diff > 1.0).sum()} | "
        f"onnx MAE on holdout = {mae:,.1f}"
    )
    if rel.max() > 0.03:
        raise RuntimeError(f"ONNX vs native deviation too large for {name}: {rel.max():.3f}")
    return float(rel.max())


def emit_backend_artifacts() -> None:
    game = pd.read_parquet(config.ML_ARTIFACTS / "game_zones.parquet")

    # Numeric zone codes must match the training encoding exactly; read them
    # back from one row per zone in the actual training dataset.
    ds = pd.read_parquet(config.ML_ARTIFACTS / "train_dataset.parquet")
    codes_rows = ds.groupby("game_zone", as_index=False).agg(code=("zone_id", "first")).sort_values("code")
    zone_codes = dict(zip(codes_rows.game_zone, codes_rows.code.astype(int)))

    codes_rows.to_parquet(config.ML_ARTIFACTS / "zone_codes.parquet", index=False)

    dump_json(config.MODELS_DIR / "feature_columns.json", {"features": list(config.FEATURE_ORDER)})
    dump_json(
        config.MODELS_DIR / "zone_codes.json",
        {"zones": zone_codes, "display_names": dict(config.GAME_ZONES)},
    )
    dump_json(
        config.MODELS_DIR / "game_zones.json",
        {
            "zones": game[["game_zone", "display_name", "centroid_lat", "centroid_lon"]].to_dict("records"),
            "turn_hours": config.TURN_HOURS,
            "horizons_h": config.HORIZON_HOURS,
        },
    )
    print("[export] wrote feature_columns.json, zone_codes.json, game_zones.json")


def main() -> None:
    X, y = features_for_check()
    for name in ["mean", "p10", "p50", "p90"]:
        out = convert(name)
        print(f"[export] {name}: {out}")
        verify(name, X, y)
    emit_backend_artifacts()


if __name__ == "__main__":
    main()