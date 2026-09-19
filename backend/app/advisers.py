"""Tool-using adviser implementations."""

from dataclasses import dataclass, field
from datetime import datetime
import logging
from typing import Any, Dict, List, Mapping, Optional, Protocol

from .adviser_tools import AdviserTools
from .data import AnalogueStore
from .models import (
    AdviserEvidence,
    AdviserForecast,
    AdviserMove,
    AdviserResponse,
    AnalogueMatch,
    HistoricalState,
    ZONE_IDS,
)


logger = logging.getLogger(__name__)


class NarrativeGenerator(Protocol):
    source: str

    def generate(self, context: Mapping[str, Any]) -> str:
        """Turn verified adviser analysis into natural-language advice."""


@dataclass(frozen=True)
class GrandpaRecommendation:
    name: str
    specialty: str
    short_recommendation: str
    confidence: float
    recommended_moves: List[AdviserMove]
    evidence: List[Dict[str, object]]
    matches_considered: int
    predicted_demand_by_zone: Mapping[str, float]
    predicted_revenue: float
    tools_used: List[str] = field(default_factory=list)


class GrandpaRat:
    """Historical-analogue analysis tool for the next three-hour block."""

    name = "Grandpa"
    specialty = "Historical analogues"

    def __init__(self, store: AnalogueStore, *, max_matches: int = 8) -> None:
        self.store = store
        self.max_matches = max_matches
        self.tools = AdviserTools(store)

    def recommend(
        self,
        state: HistoricalState,
        *,
        idle_taxis_by_zone: Optional[Mapping[str, int]] = None,
    ) -> GrandpaRecommendation:
        matches = self.tools.find_similar_periods(state, limit=self.max_matches)
        if not matches:
            return GrandpaRecommendation(
                name=self.name,
                specialty=self.specialty,
                short_recommendation="I could not find a comparable historical period.",
                confidence=0.0,
                recommended_moves=[],
                evidence=[],
                matches_considered=0,
                predicted_demand_by_zone={zone_id: 0.0 for zone_id in ZONE_IDS},
                predicted_revenue=0.0,
                tools_used=["find_similar_periods", "get_outcomes_for_similar_periods"],
            )

        weights = [match.similarity for match in matches]
        weight_total = sum(weights) or 1.0
        predicted_demand = {
            zone_id: sum(
                weight * match.next_demand_by_zone.get(zone_id, 0.0)
                for weight, match in zip(weights, matches)
            )
            / weight_total
            for zone_id in ZONE_IDS
        }
        predicted_revenue = sum(
            weight * match.next_fare_total for weight, match in zip(weights, matches)
        ) / weight_total

        target_zone = max(predicted_demand, key=predicted_demand.get)
        current_zone = max(
            state.demand_by_zone,
            key=state.demand_by_zone.get,
            default=target_zone,
        )
        increase_probability = self._increase_probability(
            state,
            matches,
            weights,
            target_zone,
        )
        confidence = min(
            0.98,
            0.25 + 0.08 * len(matches) + 0.45 * sum(weights) / weight_total,
        )
        moves = self._recommended_moves(
            predicted_demand,
            idle_taxis_by_zone or {},
        )
        evidence = self._evidence(matches, target_zone, increase_probability)
        short_recommendation = (
            f"Similar periods favor {target_zone.replace('_', ' ').title()} "
            f"for the next three hours."
        )
        if current_zone == target_zone:
            short_recommendation += " The current strongest zone is already aligned."

        return GrandpaRecommendation(
            name=self.name,
            specialty=self.specialty,
            short_recommendation=short_recommendation,
            confidence=round(confidence, 3),
            recommended_moves=moves,
            evidence=evidence,
            matches_considered=len(matches),
            predicted_demand_by_zone=predicted_demand,
            predicted_revenue=predicted_revenue,
            tools_used=[
                "find_similar_periods",
                "get_outcomes_for_similar_periods",
                "build_reposition_plan",
            ],
        )

    @staticmethod
    def _increase_probability(
        state: HistoricalState,
        matches: List[AnalogueMatch],
        weights: List[float],
        zone_id: str,
    ) -> float:
        baseline = state.demand_by_zone.get(zone_id, 0.0)
        weight_total = sum(weights) or 1.0
        increase_weight = sum(
            weight
            for weight, match in zip(weights, matches)
            if match.next_demand_by_zone.get(zone_id, 0.0) > baseline
        )
        return increase_weight / weight_total

    @staticmethod
    def _recommended_moves(
        predicted_demand: Mapping[str, float],
        idle_taxis_by_zone: Mapping[str, int],
    ) -> List[AdviserMove]:
        if not idle_taxis_by_zone:
            return []

        total_idle = sum(max(count, 0) for count in idle_taxis_by_zone.values())
        total_demand = sum(max(value, 0.0) for value in predicted_demand.values())
        if total_idle <= 0 or total_demand <= 0:
            return []

        desired = {
            zone_id: round(total_idle * max(predicted_demand.get(zone_id, 0.0), 0.0) / total_demand)
            for zone_id in ZONE_IDS
        }
        surplus = [
            [zone_id, max(idle_taxis_by_zone.get(zone_id, 0) - desired[zone_id], 0)]
            for zone_id in ZONE_IDS
        ]
        deficit = [
            [zone_id, max(desired[zone_id] - idle_taxis_by_zone.get(zone_id, 0), 0)]
            for zone_id in ZONE_IDS
        ]
        deficit.sort(
            key=lambda item: (predicted_demand.get(item[0], 0.0), item[0]),
            reverse=True,
        )

        moves: List[AdviserMove] = []
        for source, source_count in surplus:
            remaining = source_count
            for target_entry in deficit:
                target, target_count = target_entry
                if remaining <= 0 or target_count <= 0 or source == target:
                    continue
                count = min(remaining, target_count)
                moves.append(
                    AdviserMove(
                        from_zone=source,
                        to_zone=target,
                        taxi_count=count,
                    )
                )
                remaining -= count
                target_count -= count
                target_entry[1] = target_count
        return moves

    @staticmethod
    def _evidence(
        matches: List[AnalogueMatch],
        target_zone: str,
        increase_probability: float,
    ) -> List[Dict[str, object]]:
        return [
            {
                "type": "analogue_count",
                "count": len(matches),
                "text": f"Compared {len(matches)} similar historical periods.",
            },
            {
                "type": "outcome_probability",
                "zone": target_zone,
                "probability_of_increase": round(increase_probability, 3),
                "text": (
                    f"Demand increased in {target_zone.replace('_', ' ')} in "
                    f"{increase_probability:.0%} of weighted analogues."
                ),
            },
            {
                "type": "closest_period",
                "timestamp": matches[0].timestamp.isoformat(),
                "similarity": round(matches[0].similarity, 3),
                "source": matches[0].source,
            },
        ]


class GrandpaAgent:
    """Grandpa's tool-using agent boundary.

    Grandpa queries an analogue store, weighs the observed next-three-hour
    outcomes, creates a fleet plan, and turns the result into natural-language
    advice. The default narrative generator is deterministic so the API works
    without an external model; a future LLM can replace only this final
    narrative step while keeping the same response contract.
    """

    adviser_id = "grandpa"
    adviser_name = "Grandpa"
    specialty = "historical_analogues"

    def __init__(
        self,
        store: AnalogueStore,
        *,
        data_source: str = "synthetic-demo",
        narrative_generator: Optional[NarrativeGenerator] = None,
    ) -> None:
        self.store = store
        self.data_source = data_source
        self.narrative_generator = narrative_generator

    def advise(
        self,
        state: HistoricalState,
        *,
        idle_taxis_by_zone: Optional[Mapping[str, int]] = None,
        max_matches: int = 8,
    ) -> AdviserResponse:
        analysis = GrandpaRat(self.store, max_matches=max_matches).recommend(
            state,
            idle_taxis_by_zone=idle_taxis_by_zone,
        )
        source = self.data_source

        typed_evidence = self._typed_evidence(analysis.evidence, source)
        moves = [
            AdviserMove(
                from_zone=move.from_zone,
                to_zone=move.to_zone,
                taxi_count=move.taxi_count,
            )
            for move in analysis.recommended_moves
        ]
        narrative_source = "template"
        narrative = self._narrative(analysis, moves)
        if self.narrative_generator is not None:
            try:
                narrative = self.narrative_generator.generate(
                    self._narrative_context(state, analysis, typed_evidence, moves)
                )
                narrative_source = self.narrative_generator.source
            except Exception as exc:  # pragma: no cover - provider failure path
                logger.warning("Grandpa narrative generation failed: %s", exc)
                narrative_source = "template-fallback"

        return AdviserResponse(
            adviser_id=self.adviser_id,
            adviser_name=self.adviser_name,
            specialty=self.specialty,
            as_of=state.timestamp,
            horizon_hours=3,
            recommendation=narrative,
            confidence=analysis.confidence,
            moves=moves,
            evidence=typed_evidence,
            forecast=AdviserForecast(
                horizon_hours=3,
                demand_by_zone=dict(analysis.predicted_demand_by_zone),
                predicted_revenue=analysis.predicted_revenue,
            ),
            matches_considered=analysis.matches_considered,
            data_source=source,
            narrative_source=narrative_source,
            tools_used=analysis.tools_used + ["generate_natural_language_advice"],
        )

    @staticmethod
    def _narrative_context(
        state: HistoricalState,
        analysis: GrandpaRecommendation,
        evidence: List[AdviserEvidence],
        moves: List[AdviserMove],
    ) -> Dict[str, Any]:
        """Build the compact, verified payload sent to the language model."""
        return {
            "as_of": state.timestamp.isoformat(),
            "horizon_hours": 3,
            "matches_considered": analysis.matches_considered,
            "confidence": analysis.confidence,
            "forecast": {
                "demand_by_zone": dict(analysis.predicted_demand_by_zone),
                "predicted_revenue": analysis.predicted_revenue,
            },
            "moves": [GrandpaAgent._model_dump(move) for move in moves],
            "evidence": [GrandpaAgent._model_dump(item) for item in evidence],
        }

    @staticmethod
    def _model_dump(value: Any) -> Dict[str, Any]:
        """Support both Pydantic v1 and v2 during local setup transitions."""
        if hasattr(value, "model_dump"):
            return value.model_dump()
        return value.dict()

    @staticmethod
    def _narrative(
        analysis: GrandpaRecommendation,
        moves: List[AdviserMove],
    ) -> str:
        if not analysis.matches_considered:
            return "I could not find enough comparable historical periods to make a reliable recommendation."

        ranked_zones = sorted(
            analysis.predicted_demand_by_zone.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        top_zone, top_demand = ranked_zones[0]
        second_zone, second_demand = ranked_zones[1]
        move_text = "No repositioning is needed based on the current idle fleet."
        if moves:
            move_text = "Recommended moves: " + ", ".join(
                f"{move.from_zone.replace('_', ' ').title()} to "
                f"{move.to_zone.replace('_', ' ').title()} ×{move.taxi_count}"
                for move in moves
            ) + "."
        return (
            f"I compared {analysis.matches_considered} similar historical periods. "
            f"For the next three hours, {top_zone.replace('_', ' ').title()} has the "
            f"strongest analogue-weighted outlook at about {top_demand:.0f} trips, "
            f"followed by {second_zone.replace('_', ' ').title()} at about "
            f"{second_demand:.0f}. {move_text} "
            f"Expected analogue-weighted revenue is approximately "
            f"${analysis.predicted_revenue:,.0f}."
        )

    @staticmethod
    def _typed_evidence(
        evidence: List[Dict[str, object]],
        source: str,
    ) -> List[AdviserEvidence]:
        typed: List[AdviserEvidence] = []
        for item in evidence:
            kind = str(item.get("type", "analysis"))
            if kind == "analogue_count":
                typed.append(
                    AdviserEvidence(
                        kind=kind,
                        label="Historical sample",
                        detail=str(item.get("text", "")),
                        value=float(item.get("count", 0)),
                        unit="periods",
                        source=source,
                    )
                )
            elif kind == "outcome_probability":
                typed.append(
                    AdviserEvidence(
                        kind=kind,
                        label="Demand increase likelihood",
                        detail=str(item.get("text", "")),
                        zone_id=str(item.get("zone")),
                        value=float(item.get("probability_of_increase", 0.0)),
                        unit="probability",
                        source=source,
                    )
                )
            elif kind == "closest_period":
                timestamp_text = str(item.get("timestamp"))
                timestamp: Optional[datetime]
                try:
                    timestamp = datetime.fromisoformat(timestamp_text)
                except ValueError:
                    timestamp = None
                typed.append(
                    AdviserEvidence(
                        kind=kind,
                        label="Closest historical period",
                        detail=(
                            f"The closest match had similarity "
                            f"{float(item.get('similarity', 0.0)):.2f}."
                        ),
                        value=float(item.get("similarity", 0.0)),
                        unit="similarity",
                        timestamp=timestamp,
                        source=str(item.get("source", source)),
                    )
                )
            else:
                timestamp = None
                timestamp_text = item.get("timestamp")
                if timestamp_text:
                    try:
                        timestamp = datetime.fromisoformat(str(timestamp_text))
                    except ValueError:
                        timestamp = None
                value = item.get("value")
                numeric_value = float(value) if isinstance(value, (int, float)) else None
                typed.append(
                    AdviserEvidence(
                        kind=kind,
                        label=str(item.get("label", kind.replace("_", " ").title())),
                        detail=str(item.get("text", item.get("detail", ""))),
                        zone_id=(str(item["zone"]) if item.get("zone") else None),
                        value=numeric_value,
                        unit=(str(item["unit"]) if item.get("unit") else None),
                        timestamp=timestamp,
                        source=str(item.get("source", source)),
                    )
                )
        return typed


class TwitchRat:
    """Mobility adviser focused on recent demand momentum and spillover."""

    adviser_id = "twitch"
    name = "Twitch"
    specialty = "mobility_momentum"
    personality = "an impatient momentum trader who values fresh movement over old stories"

    def __init__(self, store: AnalogueStore) -> None:
        self.tools = AdviserTools(store)

    def recommend(
        self,
        state: HistoricalState,
        *,
        idle_taxis_by_zone: Optional[Mapping[str, int]] = None,
        max_matches: int = 8,
    ) -> GrandpaRecommendation:
        del max_matches
        predicted: Dict[str, float] = {}
        momentum_by_zone: Dict[str, Dict[str, float]] = {}
        flow_by_zone: Dict[str, Dict[str, object]] = {}
        neighbors_by_zone: Dict[str, Dict[str, object]] = {}
        for zone_id in ZONE_IDS:
            momentum = self.tools.calculate_momentum(state, zone_id)
            neighbors = self.tools.compare_neighboring_zones(state, zone_id)
            flow = self.tools.flow_propagation(state, zone_id)
            momentum_by_zone[zone_id] = momentum
            neighbors_by_zone[zone_id] = neighbors
            flow_by_zone[zone_id] = flow
            predicted[zone_id] = max(
                0.0,
                momentum["current"]
                + 0.65 * momentum["change"]
                + 0.20 * momentum["acceleration"]
                + 0.15 * float(flow["propagation_signal"])
                + 0.05
                * (
                    float(neighbors["neighbor_mean"])
                    - momentum["current"]
                ),
            )

        target_zone = max(predicted, key=predicted.get)
        target_momentum = momentum_by_zone[target_zone]
        target_flow = flow_by_zone[target_zone]
        target_neighbors = neighbors_by_zone[target_zone]
        sample_count = max(
            int(momentum["sample_count"])
            for momentum in momentum_by_zone.values()
        )
        confidence = min(
            0.92,
            0.25
            + 0.10 * max(sample_count - 1, 0)
            + 0.20 * min(abs(target_momentum["change"]) / 20.0, 1.0),
        )
        evidence = [
            {
                "type": "momentum",
                "label": "Demand momentum",
                "zone": target_zone,
                "value": round(target_momentum["change"], 3),
                "unit": "trips per turn",
                "text": (
                    f"{target_zone.replace('_', ' ').title()} changed by "
                    f"{target_momentum['change']:+.1f} trips versus the prior observation."
                ),
            },
            {
                "type": "flow_propagation",
                "label": "Neighbor momentum",
                "zone": target_zone,
                "value": round(float(target_flow["propagation_signal"]), 3),
                "unit": "trips per turn",
                "text": (
                    f"Nearby zones contribute a momentum signal of "
                    f"{float(target_flow['propagation_signal']):+.1f} trips per turn."
                ),
            },
            {
                "type": "neighbor_comparison",
                "label": "Current zone comparison",
                "zone": target_zone,
                "value": round(predicted[target_zone], 3),
                "unit": "predicted trips",
                "text": (
                    f"The current momentum ranking puts {target_zone.replace('_', ' ').title()} "
                    f"at the top of the short-term outlook; its neighbors average "
                    f"{float(target_neighbors['neighbor_mean']):.1f} trips."
                ),
            },
        ]
        return GrandpaRecommendation(
            name=self.name,
            specialty=self.specialty,
            short_recommendation=(
                f"Momentum is strongest in {target_zone.replace('_', ' ').title()}; "
                "follow the flow before it cools."
            ),
            confidence=round(confidence, 3),
            recommended_moves=GrandpaRat._recommended_moves(
                predicted,
                idle_taxis_by_zone or {},
            ),
            evidence=evidence,
            matches_considered=max(sample_count - 1, 0),
            predicted_demand_by_zone=predicted,
            predicted_revenue=self.tools.estimate_revenue(predicted),
            tools_used=[
                "get_recent_demand",
                "calculate_momentum",
                "compare_neighboring_zones",
                "flow_propagation",
                "build_reposition_plan",
            ],
        )


class StormyRat:
    """Weather adviser focused on weather-conditioned demand."""

    adviser_id = "stormy"
    name = "Stormy"
    specialty = "weather"
    personality = "a cautious weather watcher who thinks visibility and rain change the whole city"

    def __init__(self, store: AnalogueStore) -> None:
        self.tools = AdviserTools(store)

    def recommend(
        self,
        state: HistoricalState,
        *,
        idle_taxis_by_zone: Optional[Mapping[str, int]] = None,
        max_matches: int = 8,
    ) -> GrandpaRecommendation:
        del max_matches
        weather = self.tools.get_weather(state)
        condition = self.tools.compare_weather_condition(state)
        effects: Dict[str, Dict[str, float]] = {}
        predicted: Dict[str, float] = {}
        for zone_id in ZONE_IDS:
            effect = self.tools.get_historical_weather_effect(state, zone_id)
            effects[zone_id] = effect
            current = max(float(state.demand_by_zone.get(zone_id, 0.0)), 0.0)
            predicted[zone_id] = max(0.0, current * (1.0 + effect["effect"]))

        target_zone = max(predicted, key=predicted.get)
        target_effect = effects[target_zone]
        strongest_effect_zone = max(
            effects,
            key=lambda zone_id: effects[zone_id]["effect"],
        )
        effect_samples = max(
            int(effect["sample_count"]) for effect in effects.values()
        )
        confidence = 0.12
        if weather["available"]:
            confidence = min(
                0.90,
                0.30
                + 0.06 * min(condition.historical_samples, 8)
                + 0.05 * min(effect_samples, 8),
            )
        weather_text = condition.label
        if not weather["available"]:
            weather_text = "No usable weather signal was supplied"
        evidence = [
            {
                "type": "weather_condition",
                "label": "Current weather",
                "value": round(condition.severity, 3),
                "unit": "severity",
                "text": (
                    f"{weather_text}; observed fields: "
                    f"{', '.join(condition.observed_fields) or 'none'}."
                ),
            },
            {
                "type": "weather_effect",
                "label": "Weather-conditioned demand",
                "zone": strongest_effect_zone,
                "value": round(effects[strongest_effect_zone]["effect"], 3),
                "unit": "relative demand effect",
                "text": (
                    f"Similar weather conditions changed expected demand in "
                    f"{strongest_effect_zone.replace('_', ' ').title()} by "
                    f"{effects[strongest_effect_zone]['effect']:+.0%}."
                ),
            },
            {
                "type": "weather_sample",
                "label": "Weather history",
                "value": float(effect_samples),
                "unit": "matched periods",
                "text": (
                    f"The weather-conditioned comparison used {effect_samples} "
                    "matched historical periods at most."
                ),
            },
        ]
        return GrandpaRecommendation(
            name=self.name,
            specialty=self.specialty,
            short_recommendation=(
                f"{weather_text}. The weather-adjusted outlook is strongest in "
                f"{target_zone.replace('_', ' ').title()}."
            ),
            confidence=round(confidence, 3),
            recommended_moves=GrandpaRat._recommended_moves(
                predicted,
                idle_taxis_by_zone or {},
            ),
            evidence=evidence,
            matches_considered=effect_samples,
            predicted_demand_by_zone=predicted,
            predicted_revenue=self.tools.estimate_revenue(predicted),
            tools_used=[
                "get_weather",
                "compare_weather_condition",
                "get_historical_weather_effect",
                "build_reposition_plan",
            ],
        )


class GossipRat:
    """News and events adviser focused on unusual local activity."""

    adviser_id = "gossip"
    name = "Gossip"
    specialty = "events_news"
    personality = "a sharp-tongued local reporter who spots unusual news before everyone else"

    def __init__(self, store: AnalogueStore) -> None:
        self.tools = AdviserTools(store)

    def recommend(
        self,
        state: HistoricalState,
        *,
        idle_taxis_by_zone: Optional[Mapping[str, int]] = None,
        max_matches: int = 8,
    ) -> GrandpaRecommendation:
        del max_matches
        activity = self.tools.get_event_activity(state)
        intensity = {
            zone_id: self.tools.get_event_intensity(state, zone_id)
            for zone_id in ZONE_IDS
        }
        effects = {
            zone_id: self.tools.historical_event_effect(state, zone_id)
            for zone_id in ZONE_IDS
        }
        predicted = {
            zone_id: max(
                0.0,
                float(state.demand_by_zone.get(zone_id, 0.0))
                * (1.0 + effects[zone_id]["effect"]),
            )
            for zone_id in ZONE_IDS
        }
        target_zone = max(
            predicted,
            key=lambda zone_id: (
                intensity[zone_id]["intensity"],
                predicted[zone_id],
            ),
        )
        nearby = self.tools.get_nearby_events(state, target_zone)
        target_effect = effects[target_zone]
        event_samples = max(
            int(effect["sample_count"]) for effect in effects.values()
        )
        current_intensity = intensity[target_zone]["intensity"]
        confidence = min(
            0.88,
            0.25
            + 0.05 * min(event_samples, 8)
            + 0.15 * min(current_intensity / 20.0, 1.0),
        )
        evidence = [
            {
                "type": "event_intensity",
                "label": "Local news intensity",
                "zone": target_zone,
                "value": round(current_intensity, 3),
                "unit": "intensity score",
                "text": (
                    f"News intensity is {current_intensity:.1f} in "
                    f"{target_zone.replace('_', ' ').title()}."
                ),
            },
            {
                "type": "nearby_events",
                "label": "Nearby activity",
                "zone": target_zone,
                "value": round(float(nearby["nearby_total"]), 3),
                "unit": "nearby event activity",
                "text": (
                    f"Neighboring zones contribute {float(nearby['nearby_total']):.1f} "
                    "event-activity points."
                ),
            },
            {
                "type": "historical_event_effect",
                "label": "Historical event effect",
                "zone": target_zone,
                "value": round(target_effect["effect"], 3),
                "unit": "relative demand effect",
                "text": (
                    f"Comparable event intensity changed expected demand by "
                    f"{target_effect['effect']:+.0%} in "
                    f"{target_zone.replace('_', ' ').title()}."
                ),
            },
        ]
        return GrandpaRecommendation(
            name=self.name,
            specialty=self.specialty,
            short_recommendation=(
                f"The city is talking about {target_zone.replace('_', ' ').title()}; "
                "the local event signal deserves a measured repositioning."
            ),
            confidence=round(confidence, 3),
            recommended_moves=GrandpaRat._recommended_moves(
                predicted,
                idle_taxis_by_zone or {},
            ),
            evidence=evidence,
            matches_considered=event_samples,
            predicted_demand_by_zone=predicted,
            predicted_revenue=self.tools.estimate_revenue(predicted),
            tools_used=[
                "get_event_activity",
                "get_event_intensity",
                "get_nearby_events",
                "historical_event_effect",
                "build_reposition_plan",
            ],
        )


class AdviserAgent:
    """Shared agent boundary for all specialized rats."""

    def __init__(
        self,
        rat: Any,
        store: AnalogueStore,
        *,
        data_source: str = "synthetic-demo",
        narrative_generator: Optional[NarrativeGenerator] = None,
    ) -> None:
        self.rat = rat
        self.store = store
        self.data_source = data_source
        self.narrative_generator = narrative_generator

    def advise(
        self,
        state: HistoricalState,
        *,
        idle_taxis_by_zone: Optional[Mapping[str, int]] = None,
        max_matches: int = 8,
    ) -> AdviserResponse:
        analysis = self.rat.recommend(
            state,
            idle_taxis_by_zone=idle_taxis_by_zone,
            max_matches=max_matches,
        )
        source = self.data_source
        typed_evidence = GrandpaAgent._typed_evidence(analysis.evidence, source)
        moves = [
            AdviserMove(
                from_zone=move.from_zone,
                to_zone=move.to_zone,
                taxi_count=move.taxi_count,
            )
            for move in analysis.recommended_moves
        ]
        narrative = analysis.short_recommendation
        narrative_source = "template"
        if self.narrative_generator is not None:
            try:
                narrative = self.narrative_generator.generate(
                    self._narrative_context(state, analysis, typed_evidence, moves)
                )
                narrative_source = self.narrative_generator.source
            except Exception as exc:  # pragma: no cover - provider failure path
                logger.warning("%s narrative generation failed: %s", self.rat.name, exc)
                narrative_source = "template-fallback"

        tools_used = list(dict.fromkeys(analysis.tools_used + ["generate_natural_language_advice"]))
        return AdviserResponse(
            adviser_id=self.rat.adviser_id,
            adviser_name=self.rat.name,
            specialty=self.rat.specialty,
            as_of=state.timestamp,
            horizon_hours=3,
            recommendation=narrative,
            confidence=analysis.confidence,
            moves=moves,
            evidence=typed_evidence,
            forecast=AdviserForecast(
                horizon_hours=3,
                demand_by_zone=dict(analysis.predicted_demand_by_zone),
                predicted_revenue=analysis.predicted_revenue,
            ),
            matches_considered=analysis.matches_considered,
            data_source=source,
            narrative_source=narrative_source,
            tools_used=tools_used,
        )

    def _narrative_context(
        self,
        state: HistoricalState,
        analysis: GrandpaRecommendation,
        evidence: List[AdviserEvidence],
        moves: List[AdviserMove],
    ) -> Dict[str, Any]:
        return {
            "adviser": {
                "id": self.rat.adviser_id,
                "name": self.rat.name,
                "specialty": self.rat.specialty,
                "personality": self.rat.personality,
            },
            "as_of": state.timestamp.isoformat(),
            "horizon_hours": 3,
            "matches_considered": analysis.matches_considered,
            "confidence": analysis.confidence,
            "forecast": {
                "demand_by_zone": dict(analysis.predicted_demand_by_zone),
                "predicted_revenue": analysis.predicted_revenue,
            },
            "moves": [GrandpaAgent._model_dump(move) for move in moves],
            "evidence": [GrandpaAgent._model_dump(item) for item in evidence],
        }


class TwitchAgent(AdviserAgent):
    def __init__(
        self,
        store: AnalogueStore,
        *,
        data_source: str = "synthetic-demo",
        narrative_generator: Optional[NarrativeGenerator] = None,
    ) -> None:
        super().__init__(
            TwitchRat(store),
            store,
            data_source=data_source,
            narrative_generator=narrative_generator,
        )


class StormyAgent(AdviserAgent):
    def __init__(
        self,
        store: AnalogueStore,
        *,
        data_source: str = "synthetic-demo",
        narrative_generator: Optional[NarrativeGenerator] = None,
    ) -> None:
        super().__init__(
            StormyRat(store),
            store,
            data_source=data_source,
            narrative_generator=narrative_generator,
        )


class GossipAgent(AdviserAgent):
    def __init__(
        self,
        store: AnalogueStore,
        *,
        data_source: str = "synthetic-demo",
        narrative_generator: Optional[NarrativeGenerator] = None,
    ) -> None:
        super().__init__(
            GossipRat(store),
            store,
            data_source=data_source,
            narrative_generator=narrative_generator,
        )
