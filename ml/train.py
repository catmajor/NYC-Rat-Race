"""Train the Rat Race demand models (LightGBM).

Four models share the exact same feature matrix:
  * ``demand_mean``  -- expected (conditional mean) demand
  * ``demand_p10`` / ``demand_p50`` / ``demand_p90`` -- quantile regression

Training uses a point-in-time split (train < 2019-09-01 < val < 2019-10-01 <
holdout). Metrics (MAE / RMSE / pinball) are reported on the holdout, overall
and broken down by forecast horizon (1/2/3 h), and saved to ``model_report.json``
along with feature importances for the frontend.

Native LightGBM models and pickled sklearn wrappers are written to ``models/``;
ONNX export happens in a separate step (``ml.export``).
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib
import lightgbm as lgb
import numpy as np
import pandas as pd

from . import config
from .config import dump_json
from .data.build_train_dataset import load_store  # noqa: F401 (re-export convenience)

MODELS = {"mean": None, **{k: v for k, v in config.QUANTILE_ALPHAS.items()}}


def load_dataset() -> pd.DataFrame:
    return pd.read_parquet(config.ML_ARTIFACTS / "train_dataset.parquet")


def pinball(y, q, alpha):
    diff = np.asarray(y) - np.asarray(q)
    return float(np.mean(np.where(diff >= 0, alpha * diff, (alpha - 1) * diff)))


def fit_split(ds: pd.DataFrame, obj: str, alpha: float | None) -> lgb.LGBMRegressor:
    X = ds[config.FEATURE_ORDER]
    y = ds["target"]

    # Note: for quantile objectives we must retrain per quantile; LightGBM does
    # not support computing several quantiles in a single fit.
    params = dict(config.LGB_PARAMS)
    params["objective"] = obj
    if alpha is not None:
        params["alpha"] = alpha
        params["metric"] = "quantile"

    tr = ds["split"] == "train"
    va = ds["split"] == "validation"

    model = lgb.LGBMRegressor(**params)
    model.fit(
        X[tr],
        y[tr],
        eval_set=[(X[va], y[va])],
        callbacks=[lgb.early_stopping(150, verbose=False)],
    )
    return model


def evaluate(model: lgb.LGBMRegressor, X, y, ds) -> dict:
    pred = model.predict(X)
    out = {"mae": float(np.mean(np.abs(pred - y))), "rmse": float(np.sqrt(np.mean((pred - y) ** 2)))}
    by_horizon = {}
    for h in config.HORIZON_HOURS:
        m = ds["horizon"] == h
        e = pred[m] - y[m]
        by_horizon[str(h)] = {"mae": float(np.mean(np.abs(e))), "rmse": float(np.sqrt(np.mean(e ** 2))), "n": int(m.sum())}
    out["by_horizon"] = by_horizon
    return out


def fit_one(name: str, ds: pd.DataFrame, objective: str, alpha: float | None) -> dict:
    print(f"[train] fitting {name} (objective={objective}, alpha={alpha}) ...", flush=True)
    model = fit_split(ds, objective, alpha)
    Xh, yh = ds.loc[ds.split == "holdout", config.FEATURE_ORDER], ds.loc[ds.split == "holdout", "target"]
    metrics = evaluate(model, Xh, yh, ds[ds.split == "holdout"])
    if alpha is not None:
        q = model.predict(Xh)
        metrics["pinball"] = pinball(yh, q, alpha)
        metrics["coverage"] = float(np.mean(np.asarray(yh) <= q))

    # Feature importance (split count + gain) for the frontend.
    imp = {
        "split": dict(zip(config.FEATURE_ORDER, (model.booster_.feature_importance("split").astype(int)).tolist())),
        "gain": dict(zip(config.FEATURE_ORDER, model.booster_.feature_importance("gain").round(4).tolist())),
    }
    imp["top10"] = sorted(imp["gain"], key=imp["gain"].get, reverse=True)[:10]

    model.booster_.save_model(str(config.MODELS_DIR / f"demand_{name}.txt"))
    joblib.dump(model, config.MODELS_DIR / f"demand_{name}.joblib")
    print(f"[train]   holdout {metrics}", flush=True)
    return {"name": name, "objective": objective, "alpha": alpha, "metrics": metrics, "importance": imp, "best_iter": int(model.best_iteration_)}


def main() -> None:
    ds = load_dataset()
    print(f"[train] dataset: {len(ds):,} rows | train {int((ds.split=='train').sum()):,} val {int((ds.split=='validation').sum()):,} holdout {int((ds.split=='holdout').sum()):,}")

    report: dict = {
        "feature_order": config.FEATURE_ORDER,
        "lgb_params": config.LGB_PARAMS,
        "splits": {"train_cutoff": config.TRAIN_CUTOFF, "val_cutoff": config.VAL_CUTOFF},
        "game_zones": config.GAME_ZONES,
        "models": {},
    }

    r = fit_one("mean", ds, "regression", None)
    report["models"]["mean"] = r

    for name, alpha in config.QUANTILE_ALPHAS.items():
        r = fit_one(name, ds, "quantile", alpha)
        report["models"][name] = r

    # Ground-truth summary per horizon on the holdout (for context).
    ho = ds[ds.split == "holdout"]
    report["holdout_target_stats"] = {
        str(h): {"mean": float(ho[ho.horizon == h].target.mean()), "std": float(ho[ho.horizon == h].target.std())}
        for h in config.HORIZON_HOURS
    }

    dump_json(config.MODELS_DIR / "model_report.json", report)
    print(f"[train] report -> {config.MODELS_DIR / 'model_report.json'}")


if __name__ == "__main__":
    main()