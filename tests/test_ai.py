"""The optional AI layer, and the guardrails around it."""

from __future__ import annotations

from datetime import date

import pytest

from src.ai import build_prompt, extract_json, sanitise, select_events
from src.ai.analyzer import analyse_event, build_provider
from src.config import load_config
from src.models import Direction, Event, EventCategory, Relationship, StockImpact


def make_event(impact_score=10, relationship=Relationship.DIRECT, exposures=()):
    event = Event(
        event_id="EVENT-WAAREEENER-2026-0001",
        title="Waaree bags a 1.2 GW export order",
        event_date=date(2026, 9, 22),
        event_types=[EventCategory.EXPORT_ORDER],
    )
    event.stocks["WAAREEENER"] = StockImpact(
        ticker="WAAREEENER", relationship=relationship, impact_score=impact_score,
        direction=Direction.POSITIVE, confidence=0.8, exposures=list(exposures),
    )
    return event


def test_ai_is_off_by_default(config):
    assert config.ai_enabled is False


def test_the_pipeline_is_complete_without_ai(run_offline_result=None):
    """Covered end-to-end in test_pipeline; asserted here as an invariant."""
    config = load_config()
    assert config.get("ai.enabled") is False
    assert config.get("ai.provider")  # a provider is configured but unused


def test_only_important_events_are_escalated(config):
    events = [make_event(impact_score=3), make_event(impact_score=12)]
    chosen = select_events(events, config)
    assert len(chosen) == 1
    assert chosen[0][1].impact_score == 12


def test_complex_indirect_events_are_escalated(config):
    from src.models import ExposureMatch, ExposureType

    exposures = [
        ExposureMatch(exposure_type=ExposureType.COMMODITY, term="polysilicon",
                      relationship=Relationship.INDIRECT),
        ExposureMatch(exposure_type=ExposureType.COMPETITOR, term="LONGi",
                      relationship=Relationship.INDIRECT),
        ExposureMatch(exposure_type=ExposureType.GEOGRAPHY, term="China",
                      relationship=Relationship.WEAK),
    ]
    event = make_event(impact_score=7, relationship=Relationship.INDIRECT_STRONG,
                       exposures=exposures)
    assert select_events([event], config)


def test_prompt_contains_the_company_and_the_schema():
    prompt = build_prompt({
        "company": "Waaree Energies Limited", "ticker": "WAAREEENER",
        "industry": "Solar", "exchange": "NSE", "exposures": "- none",
        "title": "Order win", "event_date": "2026-09-22", "categories": "EXPORT_ORDER",
        "relationship": "DIRECT", "impact_score": 11, "direction": "POSITIVE",
        "summaries": "- none",
    })
    assert "Waaree Energies Limited" in prompt
    assert "revenue_effect" in prompt
    assert "monitor_next" in prompt
    assert "Do not recommend" in prompt


def test_json_is_extracted_from_a_chatty_response():
    parsed = extract_json('Sure!\n```json\n{"revenue_effect": "higher"}\n```')
    assert parsed == {"revenue_effect": "higher"}
    assert extract_json("no json here") is None


def test_recommendations_are_stripped():
    cleaned, redactions = sanitise({
        "revenue_effect": "Investors should BUY this stock now",
        "monitor_next": ["order book", "target price of 500"],
    })
    assert "buy" not in cleaned["revenue_effect"].lower()
    assert cleaned["monitor_next"] == ["order book"]
    assert redactions


def test_the_schema_is_always_complete():
    cleaned, _ = sanitise({"revenue_effect": "higher"})
    for key in ("margin_effect", "cost_effect", "key_uncertainty", "monitor_next"):
        assert key in cleaned


def test_a_dead_model_does_not_break_the_run(config, watchlist):
    class DeadProvider:
        name = "dead"

        def complete(self, system, prompt):
            raise RuntimeError("connection refused")

    event = make_event()
    result = analyse_event(DeadProvider(), event, event.stocks["WAAREEENER"],
                           watchlist.get("WAAREEENER"), config)
    assert not result.ok
    assert "connection refused" in result.error


def test_a_garbage_response_is_rejected(config, watchlist):
    class BabblingProvider:
        name = "babbler"

        def complete(self, system, prompt):
            return "I think this is probably fine, no JSON though"

    event = make_event()
    result = analyse_event(BabblingProvider(), event, event.stocks["WAAREEENER"],
                           watchlist.get("WAAREEENER"), config)
    assert not result.ok


def test_a_good_response_is_attached(config, watchlist):
    class GoodProvider:
        name = "good"

        def complete(self, system, prompt):
            return (
                '{"revenue_effect": "material addition to FY27 revenue", '
                '"margin_effect": "unclear", "monitor_next": ["execution schedule"]}'
            )

    event = make_event()
    result = analyse_event(GoodProvider(), event, event.stocks["WAAREEENER"],
                           watchlist.get("WAAREEENER"), config)
    assert result.ok
    assert "FY27" in result.data["revenue_effect"]
    assert result.data["monitor_next"] == ["execution schedule"]


def test_unknown_provider_returns_none():
    config = load_config(overrides={"ai": {"provider": "definitely-not-a-provider"}})
    assert build_provider(config) is None
