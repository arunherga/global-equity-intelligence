"""Normalisation of titles, URLs and dates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.models import Article, SourceType
from src.normalize import (
    canonical_url,
    clean_text,
    domain_of,
    normalize_article,
    parse_date,
    strip_publisher_suffix,
    within_lookback,
)


def test_publisher_suffix_is_stripped():
    title, publisher = strip_publisher_suffix("Waaree bags a 1 GW order - The Economic Times")
    assert title == "Waaree bags a 1 GW order"
    assert publisher == "The Economic Times"


def test_a_real_headline_is_not_truncated():
    title, publisher = strip_publisher_suffix(
        "Coal India output rises - here is what the numbers actually mean for the sector."
    )
    assert publisher == ""
    assert title.endswith("sector.")


def test_html_and_entities_are_cleaned():
    assert clean_text("<p>Waaree &amp; partners&#39; deal</p>") == "Waaree & partners' deal"


def test_tracking_parameters_are_dropped():
    url = canonical_url("https://Example.test/story/1?utm_source=x&id=7&fbclid=y")
    assert url == "https://example.test/story/1?id=7"


def test_redirect_wrappers_are_unwrapped():
    wrapped = "https://news.google.com/rss/articles/CBMi?url=https%3A%2F%2Fpub.test%2Fa%2Fb"
    assert canonical_url(wrapped) == "https://pub.test/a/b"


def test_domain_extraction():
    assert domain_of("https://www.reuters.com/markets/x") == "reuters.com"
    assert domain_of("not a url") == ""


@pytest.mark.parametrize(
    "raw",
    [
        "Mon, 22 Sep 2026 10:30:00 GMT",
        "20260922T103000Z",
        "22-Sep-2026 10:30:00",
        "2026-09-22T10:30:00Z",
        "2026-09-22 10:30:00",
    ],
)
def test_date_formats_across_sources(raw):
    parsed = parse_date(raw)
    assert parsed is not None
    assert parsed.date().isoformat() == "2026-09-22"


def test_unparseable_dates_return_none():
    assert parse_date("sometime last week") is None
    assert parse_date("") is None


def test_source_type_is_inferred_from_the_domain(config):
    article = Article(title="Test story", url="https://www.reuters.com/markets/story-1")
    normalize_article(article, config)
    assert article.source_type == SourceType.REUTERS


def test_exchange_filings_are_marked_official(config):
    article = Article(title="Intimation", url="https://www.nseindia.com/filing/1")
    normalize_article(article, config)
    assert article.is_official


def test_lookback_window(now):
    recent = Article(title="x", url="https://a.test/1", published=now - timedelta(hours=5))
    old = Article(title="y", url="https://a.test/2", published=now - timedelta(days=9))
    undated = Article(title="z", url="https://a.test/3")
    assert within_lookback(recent, 36, now=now)
    assert not within_lookback(old, 36, now=now)
    assert within_lookback(undated, 36, now=now)
