"""Clustering articles into Events — the core design principle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.classify import classify_article
from src.event_cluster import (
    MatchedArticle,
    build_event,
    can_cluster,
    cluster_articles,
    make_event_id,
)
from src.models import Relationship, SourceType
from src.relationship import StockLink


def matched(article, ticker="WAAREEENER", relationship=Relationship.DIRECT):
    return MatchedArticle(
        article=article,
        links=[StockLink(ticker=ticker, relationship=relationship)],
        classification=classify_article(article),
    )


def test_one_development_from_four_sources_becomes_one_event(make_article):
    items = [
        matched(make_article(
            "Waaree secures a large US module order", domain="wire.test",
            source_type=SourceType.REUTERS)),
        matched(make_article(
            "Waaree Energies wins a big order from a United States developer",
            domain="paper.test", source_type=SourceType.MAJOR_FINANCIAL_PRESS)),
        matched(make_article(
            "Waaree Energies Limited announces receipt of an order", domain="nse.test",
            source_type=SourceType.COMPANY_EXCHANGE_FILING, official=True)),
        matched(make_article(
            "Waaree Energies: press release on receipt of order", domain="ir.test",
            source_type=SourceType.COMPANY_IR, official=True)),
    ]
    clusters = cluster_articles(items)
    assert len(clusters) == 1

    event = build_event(clusters[0], make_event_id(clusters[0], 1))
    assert event.article_count == 4
    assert event.source_count == 4
    assert event.has_official_source()


def test_the_official_filing_becomes_the_primary_source(make_article):
    items = [
        matched(make_article("Waaree wins an order", domain="wire.test")),
        matched(make_article(
            "Waaree Energies Limited announces receipt of order", domain="nse.test",
            source_type=SourceType.COMPANY_EXCHANGE_FILING, official=True)),
    ]
    cluster = cluster_articles(items)[0]
    event = build_event(cluster, make_event_id(cluster, 1))
    assert event.sources[0].is_official


def test_unrelated_stories_about_one_company_stay_separate(make_article):
    items = [
        matched(make_article("Waaree wins a 1 GW export order", domain="a.test")),
        matched(make_article("Waaree commissions a new 5.4 GW cell plant", domain="b.test")),
    ]
    assert len(cluster_articles(items)) == 2


def test_different_companies_never_cluster(make_article):
    items = [
        matched(make_article("Coal India raises production guidance", domain="a.test"),
                ticker="COALINDIA"),
        matched(make_article("HDFC Bank reports deposit growth", domain="b.test"),
                ticker="HDFCBANK"),
    ]
    assert len(cluster_articles(items)) == 2


def test_time_window_is_enforced(make_article):
    old = matched(make_article("Waaree wins a US module order", domain="a.test", hours_ago=24 * 30))
    new = matched(make_article("Waaree wins a US module order", domain="b.test", hours_ago=1))
    ok, _, _ = can_cluster(new, old, threshold=0.62, window_days=3)
    assert not ok


def test_event_id_scopes_to_one_ticker_or_global(make_article):
    single = cluster_articles([matched(make_article("Waaree wins an order"))])[0]
    assert make_event_id(single, 7).startswith("EVENT-WAAREEENER-")
    assert make_event_id(single, 7).endswith("-0007")

    shared = MatchedArticle(
        article=make_article("RBI cuts the repo rate"),
        links=[
            StockLink(ticker="TMB", relationship=Relationship.INDIRECT),
            StockLink(ticker="HDFCBANK", relationship=Relationship.INDIRECT),
        ],
        classification=classify_article(make_article("RBI cuts the repo rate")),
    )
    cluster = cluster_articles([shared])[0]
    assert "GLOBAL" in make_event_id(cluster, 1)


def test_other_is_dropped_when_a_real_category_exists(make_article):
    items = [
        matched(make_article("Waaree Energies Limited: intimation of receipt of order",
                             domain="a.test")),
        matched(make_article("Waaree secures a large export order", domain="b.test")),
    ]
    cluster = cluster_articles(items)[0]
    from src.models import EventCategory

    assert EventCategory.OTHER not in cluster.categories


def test_clustering_is_deterministic(make_article):
    items = [
        matched(make_article("Waaree wins a US module order", domain=f"{i}.test"))
        for i in range(5)
    ]
    first = [sorted(a.article.source_domain for a in c.articles) for c in cluster_articles(items)]
    second = [sorted(a.article.source_domain for a in c.articles) for c in cluster_articles(items)]
    assert first == second
