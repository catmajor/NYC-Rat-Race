"""Shared OpenAI narrative client for adviser rats."""

from __future__ import annotations

import json
from typing import Mapping, Any

from pydantic import BaseModel, Field


class AdviserNarrative(BaseModel):
    """The only field the LLM is allowed to author for the adviser response."""

    advice: str = Field(min_length=1)


class OpenAINarrativeGenerator:
    """Generate adviser personality while keeping numerical facts deterministic."""

    source_prefix = "openai:"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gpt-4o-mini",
        temperature: float = 0.8,
    ) -> None:
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAINarrativeGenerator requires the backend llm extra"
            ) from exc

        self.client = OpenAI(api_key=api_key)
        self.model = model
        self.temperature = temperature
        self.source = self.source_prefix + model

    def generate(self, context: Mapping[str, Any]) -> str:
        adviser = context.get("adviser", {})
        adviser_name = str(adviser.get("name", "Grandpa"))
        specialty = str(adviser.get("specialty", "historical analogues"))
        personality = str(
            adviser.get(
                "personality",
                "a warm but skeptical NYC taxi historian",
            )
        )
        response = self.client.responses.parse(
            model=self.model,
            temperature=self.temperature,
            input=[
                {
                    "role": "developer",
                    "content": (
                        f"You are {adviser_name}, an adviser specializing in {specialty}. "
                        f"Your personality is {personality}. Turn the supplied verified "
                        "analysis into concise natural-language advice for a taxi-fleet manager. Keep the personality distinctive "
                        "but practical. Do not invent numbers, zones, historical periods, "
                        "or evidence. Mention uncertainty when confidence is low. Give "
                        "2 to 4 sentences. The structured output must contain only advice."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(context, default=str, ensure_ascii=False),
                },
            ],
            text_format=AdviserNarrative,
        )
        parsed = response.output_parsed
        if parsed is None or not parsed.advice.strip():
            raise RuntimeError("The LLM returned no adviser narrative")
        return parsed.advice.strip()
