"""Persistence, event updates and the historical query interface."""

from __future__ import annotations

from datetime import date

import pytest

from src.event_store import EventStore
from src.models import (
    Direction,
    Event,
    EventCategory,
    EventSource,
    PriceReaction,
    Relationship,
    SourceType,
    StockImpact,
)


def make_event(event_id, title, day, categories, ticker="SUPRIYA", impact=6, official=False):
    event = Event(
        event_id=event_id,
        title=title,
        event_date=day,
        event_types=list(categories),
        cluster_key=f"{ticker}|{categories[0].value}|{event_id}",
        sources=[
            EventSource(
                title=title,
                url=f"https://src.test/{event_id}",
                source_name="NSE" if official else "Paper",
                source_domain="nse.test" if official else "paper.test",
                source_type=(
                    SourceType.COMPANY_EXCHANGE_FILING if official
                    else SourceType.ESTABLISHED_NEWSPAPER
                ),
                is_official=official,
            )
        ],
    )
    event.stocks[ticker] = StockImpact(
        ticker=ticker, relationship=Relationship.DIRECT, impact_score=impact,
        direction=Direction.UNCERTAIN, confidence=0.5,
    )
    return event


@pytest.fixture
def store(tmp_path):
    return EventStore(tmp_path / "events").load()


def test_save_and_reload(store, tmp_path):
    event = make_event("EVENT-SUPRIYA-2026-0001", "Capacity expansion planned",
                       date(2026, 9, 20), [EventCategory.CAPACITY_EXPANSION])
    store.save(event)
    store.save_index()

    reloaded = EventStore(tmp_path / "events").load()
    assert reloaded.get("EVENT-SUPRIYA-2026-0001").title == "Capacity expansion planned"


def test_events_are_filed_by_year_and_scope(store):
    event = make_event("EVENT-SUPRIYA-2026-0001", "x", date(2026, 9, 20),
                       [EventCategory.REGULATORY])
    path = store.save(event)
    assert path.parent.name == "SUPRIYA"
    assert path.parent.parent.name == "2026"


def test_a_story_that_develops_updates_the_same_event(store):
    """Rumour on day 1, board approval on day 3: one event, not two."""
    rumour = make_event("EVENT-SUPRIYA-2026-0001",
                        "Supriya may expand capacity, sources say",
                        date(2026, 9, 20), [EventCategory.CAPACITY_EXPANSION])
    store.save(rumour)
    store.save_index()

    confirmation = make_event("EVENT-SUPRIYA-2026-0002",
                              "Supriya Lifescience board approves capacity expansion",
                              date(2026, 9, 22), [EventCategory.CAPACITY_EXPANSION],
                              impact=9, official=True)
    existing = store.find_existing(confirmation)
    assert existing is not None
    assert existing.event_id == "EVENT-SUPRIYA-2026-0001"

    merged = store.merge(existing, confirmation)
    assert merged.article_count == 2
    assert merged.stocks["SUPRIYA"].impact_score == 9
    assert merged.title == "Supriya Lifescience board approves capacity expansion"
    changes = [h.change for h in merged.history]
    assert "sources added" in changes
    assert "official confirmation" in changes
    assert "impact raised" in changes


def test_unrelated_events_are_not_merged(store):
    first = make_event("EVENT-SUPRIYA-2026-0001", "Supriya receives a USFDA warning letter",
                       date(2026, 9, 20), [EventCategory.REGULATORY])
    store.save(first)
    store.save_index()
    other = make_event("EVENT-SUPRIYA-2026-0002", "Supriya declares an interim dividend",
                       date(2026, 9, 22), [EventCategory.DIVIDEND])
    assert store.find_existing(other) is None


def test_sequence_numbers_do_not_collide(store):
    store.save(make_event("EVENT-SUPRIYA-2026-0001", "a", date(2026, 1, 2),
                          [EventCategory.OTHER]))
    store.save(make_event("EVENT-SUPRIYA-2026-0007", "b", date(2026, 1, 3),
                          [EventCategory.OTHER]))
    assert store.next_sequence("SUPRIYA", 2026) == 8
    assert store.next_sequence("SUPRIYA", 2027) == 1


def test_history_survives_the_round_trip(store, tmp_path):
    event = make_event("EVENT-SUPRIYA-2026-0001", "x", date(2026, 9, 20),
                       [EventCategory.REGULATORY])
    event.record("created", "first sighting")
    store.save(event)
    store.save_index()
    reloaded = EventStore(tmp_path / "events").load().get("EVENT-SUPRIYA-2026-0001")
    assert [h.change for h in reloaded.history] == ["created"]


def test_price_reaction_fields_are_reserved(store, tmp_path):
    event = make_event("EVENT-SUPRIYA-2026-0001", "x", date(2026, 9, 20),
                       [EventCategory.REGULATORY])
    event.price_reaction["SUPRIYA"] = PriceReaction(price_at_event=511.4, return_1d=-0.031)
    store.save(event)
    store.save_index()
    reloaded = EventStore(tmp_path / "events").load().get("EVENT-SUPRIYA-2026-0001")
    assert reloaded.price_reaction["SUPRIYA"].return_1d == pytest.approx(-0.031)
    assert reloaded.price_reaction["SUPRIYA"].return_60d is None


def test_historical_research_queries(store):
    store.save(make_event("EVENT-WAAREEENER-2026-0001", "US raises solar tariffs",
                          date(2026, 3, 1), [EventCategory.TARIFF], ticker="WAAREEENER",
                          impact=11))
    store.save(make_event("EVENT-WAAREEENER-2026-0002", "Waaree launches a new module",
                          date(2026, 4, 1), [EventCategory.NEW_PRODUCT], ticker="WAAREEENER",
                          impact=4))
    store.save_index()

    tariffs = store.query(ticker="WAAREEENER", categories=["TARIFF"])
    assert [e.event_id for e in tariffs] == ["EVENT-WAAREEENER-2026-0001"]

    important = store.query(ticker="WAAREEENER", min_impact=10)
    assert len(important) == 1

    windowed = store.query(ticker="WAAREEENER", since=date(2026, 3, 15))
    assert [e.event_id for e in windowed] == ["EVENT-WAAREEENER-2026-0002"]


def test_a_corrupt_index_is_rebuilt(store, tmp_path):
    store.save(make_event("EVENT-SUPRIYA-2026-0001", "x", date(2026, 9, 20),
                          [EventCategory.OTHER]))
    store.save_index()
    (tmp_path / "events" / "index.json").write_text("{ not json", encoding="utf-8")
    recovered = EventStore(tmp_path / "events").load()
    assert "EVENT-SUPRIYA-2026-0001" in recovered.index


def test_stats(store):
    store.save(make_event("EVENT-SUPRIYA-2026-0001", "x", date(2026, 9, 20),
                          [EventCategory.OTHER]))
    store.save_index()
    assert store.stats()["events"] == 1
    assert store.stats()["by_ticker"]["SUPRIYA"] == 1
