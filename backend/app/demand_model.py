"""Runtime ONNX demand predictions for the game turns."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Mapping

import numpy as np
import onnxruntime as ort


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = REPOSITORY_ROOT / "ml" / "models"


class DemandModel:
    """Load the exported demand models and predict one value per game zone."""

    def __init__(self) -> None:
        with (MODEL_DIR / "feature_columns.json").open(encoding="utf-8") as source:
            self.features = json.load(source)["features"]
        with (MODEL_DIR / "zone_codes.json").open(encoding="utf-8") as source:
            self.zone_codes = json.load(source)["zones"]
        self.sessions = {
            name: ort.InferenceSession(
                str(MODEL_DIR / f"demand_{name}.onnx"),
                providers=["CPUExecutionProvider"],
            )
            for name in ("mean", "p10", "p90")
        }

    @staticmethod
    def _value(values: Mapping[str, float], *keys: str) -> float:
        for key in keys:
            value = values.get(key)
            if value is not None:
                return float(value)
        return 0.0

    def _row(
        self,
        timestamp: datetime,
        zone_id: str,
        weather: Mapping[str, float],
        events: Mapping[str, float],
    ) -> list[float]:
        values = {
            "hour": float(timestamp.hour),
            "dow": float(timestamp.weekday()),
            "temp_c": self._value(weather, "temperature_c", "temp_c"),
            "wind_ms": self._value(weather, "wind_mps", "wind_ms"),
            "vis_km": self._value(weather, "visibility_km", "vis_km")
            or self._value(weather, "visibility_m") / 1000.0,
            "precip_mm": self._value(weather, "rain_mm", "precip_mm"),
            "precip_3h_mm": self._value(weather, "rain_mm", "precip_3h_mm"),
            "event_count": self._value(events, "event_count"),
            "event_mentions": self._value(events, "event_mentions", "news_volume"),
            "avg_tone": self._value(events, "avg_tone"),
            "avg_goldstein": self._value(events, "avg_goldstein"),
            "zone_event_count": self._value(
                events, f"zone_event_count:{zone_id}", f"event_count:{zone_id}"
            ),
            "zone_event_mentions": self._value(
                events, f"zone_event_mentions:{zone_id}", f"event_mentions:{zone_id}"
            ),
            "zone_avg_tone": self._value(events, f"zone_avg_tone:{zone_id}", f"avg_tone:{zone_id}"),
            "zone_avg_goldstein": self._value(
                events, f"zone_avg_goldstein:{zone_id}", f"avg_goldstein:{zone_id}"
            ),
            "horizon": 1.0,
            "zone_id": float(self.zone_codes[zone_id]),
        }
        return [values[name] for name in self.features]

    def predict(
        self,
        timestamp: datetime,
        weather_by_zone: Mapping[str, Mapping[str, float]],
        events: Mapping[str, float],
        zone_ids: list[str],
    ) -> tuple[Dict[str, float], float]:
        rows = np.asarray(
            [self._row(timestamp, zone_id, weather_by_zone[zone_id], events) for zone_id in zone_ids],
            dtype=np.float32,
        )
        predictions: Dict[str, np.ndarray] = {}
        for name, session in self.sessions.items():
            input_name = session.get_inputs()[0].name
            predictions[name] = session.run(None, {input_name: rows})[0].reshape(-1)
        mean = np.maximum(predictions["mean"], 0.0)
        interval = float(np.mean(np.maximum(predictions["p90"] - predictions["p10"], 0.0)))
        scale = float(np.mean(mean)) or 1.0
        confidence = max(0.5, min(0.99, 1.0 - interval / (scale * 4.0)))
        return {zone_id: round(float(value), 2) for zone_id, value in zip(zone_ids, mean)}, round(confidence, 3)
