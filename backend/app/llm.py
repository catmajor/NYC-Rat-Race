"""Shared Gemini decision client for adviser rats.

Each adviser keeps all numerical analysis in Python/Polars/DuckDB. The language
model only receives compact, verified analysis results and authors the
personality-specific recommendation text plus a confidence value. Every number
that leaves the adviser is derived deterministically from the verified tools;
the model never writes zone counts, fares, or evidence.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from pydantic import BaseModel, Field


class AdviserDecision(BaseModel):
    """The only fields the language model authors for an adviser response.

    ``advice`` drives the natural-language recommendation. ``confidence`` is a
    soft, model-set belief in the advice, constrained to 0..1 by validation; it
    never moves taxis by itself.
    """

    advice: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


@dataclass(frozen=True)
class AdviserDecisionResult:
    text: str
    confidence: float


class GeminiNarrativeGenerator:
    """Generate adviser personality while keeping numerical facts deterministic."""

    source_prefix = "gemini:"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-2.5-flash",
        temperature: float = 0.8,
        timeout_seconds: float = 20.0,
    ) -> None:
        try:
            from google import genai
        except ImportError as exc:
            raise RuntimeError(
                "GeminiNarrativeGenerator requires the backend llm extra"
            ) from exc

        self.client = genai.Client(api_key=api_key, http_options=genai.types.HttpOptions(timeout=timeout_seconds * 1000))
        self.model = model
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self.source = self.source_prefix + model

    def decide(self, context: Mapping[str, Any]) -> AdviserDecisionResult:
        adviser = context.get("adviser", {})
        adviser_name = str(adviser.get("name", "Grandpa"))
        specialty = str(adviser.get("specialty", "historical analogues"))
        personality = str(
            adviser.get(
                "personality",
                "a warm but skeptical NYC taxi historian",
            )
        )
        system_instruction = (
            f"You are {adviser_name}, an adviser specializing in {specialty}. "
            f"Your personality is {personality}. Interpret the supplied verified "
            "analysis for a taxi-fleet manager and decide what to recommend. "
            "Keep the personality distinctive but practical. Do not invent "
            "numbers, zones, historical periods, or evidence. Mention "
            "uncertainty when the analysis is weak. Give 2 to 4 sentences of "
            "direct advice. Set confidence proportional to the strength of the "
            "evidence in context; 0 means no signal, 1 means very strong signal."
        )
        response = self.client.models.generate_content(
            model=self.model,
            contents=json.dumps(context, default=str, ensure_ascii=False),
            config=self._build_config(system_instruction),
        )
        decision = AdviserDecision.model_validate_json(response.text)
        if not decision.advice.strip():
            raise RuntimeError("Gemini returned no adviser advice")
        return AdviserDecisionResult(
            text=decision.advice.strip(),
            confidence=decision.confidence,
        )

    def _build_config(self, system_instruction: str):
        """Build the structured-output request config for the chosen SDK."""
        from google.genai import types

        return types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json",
            response_schema=AdviserDecision,
            temperature=self.temperature,
        )

    def generate(self, context: Mapping[str, Any]) -> str:
        """Narrative-only path used by the shared agent boundary."""
        return self.decide(context).text