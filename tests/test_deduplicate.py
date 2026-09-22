"""Article deduplication."""

from __future__ import annotations

from src.deduplicate import deduplicate
from src.models import SourceType


def test_identical_urls_collapse(make_article, config):
    articles = [
        make_article("Waaree wins a 1 GW order", url="https://a.test/story"),
        make_article("Waaree wins a 1 GW order", url="https://a.test/story?utm_source=x"),
    ]
    result = deduplicate(articles, quality_map=config.source_quality)
    assert result.kept == 1
    assert result.removed == 1


def test_same_publisher_reruns_collapse(make_article, config):
    articles = [
        make_article("Waaree wins a 1 GW order", url="https://a.test/1", domain="a.test"),
        make_article("Waaree wins a 1 GW order!", url="https://a.test/2", domain="a.test"),
    ]
    result = deduplicate(articles, quality_map=config.source_quality)
    assert result.kept == 1


def test_different_publishers_are_kept_as_independent_sources(make_article, config):
    """Three papers running one wire story is evidence, not duplication.

    Collapsing them here would destroy the "three or more independent
    sources" scoring signal, so they survive to become one Event with three
    sources instead.
    """
    articles = [
        make_article("Waaree wins a 1 GW order", domain="a.test"),
        make_article("Waaree wins a 1 GW order", domain="b.test"),
        make_article("Waaree wins a 1 GW order", domain="c.test"),
    ]
    result = deduplicate(articles, quality_map=config.source_quality)
    assert result.kept == 3


def test_the_better_copy_survives(make_article, config):
    plain = make_article("Waaree order", url="https://a.test/x", domain="a.test")
    official = make_article(
        "Waaree order",
        url="https://a.test/x",
        domain="a.test",
        source_type=SourceType.COMPANY_EXCHANGE_FILING,
        official=True,
    )
    result = deduplicate([plain, official], quality_map=config.source_quality)
    assert result.kept == 1
    assert result.articles[0].is_official


def test_distinct_stories_are_not_merged(make_article, config):
    articles = [
        make_article("Waaree wins a 1 GW order", domain="a.test"),
        make_article("Coal India raises production guidance", domain="a.test"),
    ]
    result = deduplicate(articles, quality_map=config.source_quality)
    assert result.kept == 2


def test_empty_input_is_safe(config):
    result = deduplicate([], quality_map=config.source_quality)
    assert result.kept == 0 and result.removed == 0
