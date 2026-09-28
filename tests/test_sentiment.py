"""Consumer signal: a trend, deliberately not an Event."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.models import Article, SourceType
from src.sentiment import (
    ConsumerSignal,
    SentimentStore,
    SignalChange,
    analyse,
    compare,
    polarity,
)


def post(title, *, ticker="RAVEL", term="Ravel PRO", score=10, comments=4, body=""):
    article = Article(
        title=title, summary=body,
        url=f"https://www.reddit.com/r/IndianHaircare/c/{abs(hash(title))}/",
        source_name="r/IndianHaircare", source_domain="reddit.com",
        source_type=SourceType.SOCIAL_MEDIA, tickers_hint=[ticker],
        published=datetime(2026, 9, 28, tzinfo=timezone.utc),
    )
    article.raw.update({
        "consumer": True, "score": score, "num_comments": comments,
        "query_ticker": ticker, "query_term": term,
    })
    return article


# -- polarity -------------------------------------------------------------


@pytest.mark.parametrize("text,expected", [
    ("Absolutely love this shampoo, will repurchase", "positive"),
    ("Waste of money, caused a rash, asking for a refund", "negative"),
    ("Has anyone tried this?", "neutral"),
    ("", "neutral"),
    ("Arrived damaged and the seller refused a return", "negative"),
    ("Works well and worth the money", "positive"),
])
def test_polarity_sorts_plain_cases(text, expected):
    assert polarity(text) == expected


def test_polarity_never_raises_on_odd_input():
    for value in (None, "   ", "🙂🙂🙂", "a" * 5000):
        assert polarity(value) in {"positive", "negative", "neutral"}


# -- aggregation ----------------------------------------------------------


def test_mentions_are_aggregated_per_company():
    signals = analyse([
        post("Love the Ravel PRO serum"),
        post("Ravel PRO gave me a rash and I want a refund"),
        post("HexL loaders on site?", ticker="JKIPL", term="HexL backhoe"),
    ])

    by_ticker = {s.ticker: s for s in signals}
    assert by_ticker["RAVEL"].mentions == 2
    assert by_ticker["RAVEL"].positive == 1
    assert by_ticker["RAVEL"].negative == 1
    assert by_ticker["JKIPL"].mentions == 1
    assert signals[0].ticker == "RAVEL", "busiest first"


def test_engagement_counts_votes_and_comments():
    signals = analyse([post("x", score=30, comments=12)])

    assert signals[0].engagement == 42


def test_only_three_examples_are_kept():
    """The report is a summary; a feed of anecdotes is the failure mode."""
    signals = analyse([post(f"Ravel PRO thought number {n}") for n in range(10)])

    assert signals[0].mentions == 10
    assert len(signals[0].examples) == 3


def test_a_handful_of_mentions_is_not_read_as_a_leaning():
    """Two angry posts is two people, not a product problem."""
    quiet = ConsumerSignal(ticker="RAVEL", mentions=2, negative=2)

    assert quiet.leaning == "too few to read"


def test_a_clear_run_of_complaints_is_read_as_negative():
    loud = ConsumerSignal(ticker="RAVEL", mentions=20, positive=3, negative=12, neutral=5)

    assert loud.leaning == "mostly negative"
    assert loud.net == -9


# -- change detection -----------------------------------------------------


def test_a_jump_from_a_real_baseline_is_notable():
    change = SignalChange(
        signal=ConsumerSignal(ticker="RAVEL", mentions=12, negative=8, positive=1),
        previous_mentions=4, previous_net=1,
    )

    assert change.mention_change == 8
    assert change.is_notable


def test_a_jump_from_almost_nothing_is_not_notable():
    """One mention to three is noise wearing a 200% rise as a costume."""
    change = SignalChange(
        signal=ConsumerSignal(ticker="RAVEL", mentions=3, neutral=3),
        previous_mentions=1, previous_net=0,
    )

    assert not change.is_notable


def test_a_souring_tone_is_notable_even_without_more_volume():
    change = SignalChange(
        signal=ConsumerSignal(ticker="RAVEL", mentions=8, positive=1, negative=6),
        previous_mentions=8, previous_net=2,
    )

    assert change.mention_change == 0
    assert change.is_notable, "the volume held but the mood turned"


def test_a_company_with_no_history_compares_against_zero():
    changes = compare([ConsumerSignal(ticker="RAVEL", mentions=5)], [])

    assert changes[0].previous_mentions == 0
    assert changes[0].mention_change == 5


# -- persistence ----------------------------------------------------------


def test_signals_survive_a_round_trip(tmp_path):
    store = SentimentStore(tmp_path)
    signals = analyse([post("Ravel PRO is great"), post("Ravel PRO is terrible")])

    store.save(date(2026, 9, 28), signals)
    loaded = store.load(date(2026, 9, 28))

    assert [s.ticker for s in loaded] == [s.ticker for s in signals]
    assert loaded[0].mentions == signals[0].mentions
    assert loaded[0].examples == signals[0].examples


def test_the_previous_day_is_found_however_long_ago(tmp_path):
    """Runs are not daily in practice; 'yesterday' may be last week."""
    store = SentimentStore(tmp_path)
    store.save(date(2026, 9, 1), [ConsumerSignal(ticker="RAVEL", mentions=4)])
    store.save(date(2026, 9, 20), [ConsumerSignal(ticker="RAVEL", mentions=9)])

    previous = store.most_recent_before(date(2026, 9, 28))

    assert previous[0].mentions == 9, "the latest earlier day, not the oldest"


def test_missing_or_corrupt_history_is_not_fatal(tmp_path):
    store = SentimentStore(tmp_path)

    assert store.load(date(2026, 9, 28)) == []
    assert store.most_recent_before(date(2026, 9, 28)) == []

    store.root.mkdir(parents=True, exist_ok=True)
    (store.root / "2026-09-01.json").write_text("{ not json", encoding="utf-8")

    assert store.most_recent_before(date(2026, 9, 28)) == []


# -- the boundary that matters -------------------------------------------


def test_the_sentiment_module_touches_no_event_machinery():
    """It must stay deletable without the core noticing."""
    import pathlib

    source = pathlib.Path("src/sentiment.py").read_text(encoding="utf-8")

    for forbidden in ("impact", "Event", "cluster", "score_impact", "direction"):
        assert f"import {forbidden}" not in source
    assert "from .models import Article" in source
    assert "Event" not in source.split('"""', 2)[2], "no Event usage in the code body"
