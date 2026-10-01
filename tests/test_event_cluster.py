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


# -- the plaintiff-firm press-release mill -------------------------------
#
# One securities class action is announced by every firm chasing it, each
# writing its own headline. They share no wording beyond the company name,
# so title similarity measures nothing useful here. On 2026-09-30 the HDFC
# Bank action came out as four separate events.

import pytest as _pytest


def _notice(title, day=18, ticker="HDFCBANK", summary=""):
    from datetime import datetime, timezone
    from src.classify import classify_article
    from src.event_cluster import MatchedArticle
    from src.models import Article, Relationship
    from src.relationship import StockLink

    article = Article(
        title=title, summary=summary,
        url=f"https://example.test/{abs(hash(title))}", source_domain="example.test",
        published=datetime(2026, 9, day, 9, 0, tzinfo=timezone.utc),
    )
    link = StockLink(ticker=ticker, relationship=Relationship.DIRECT, entity=None,
                     exposures=[], reasons=[], evidence_weight=4.0)
    return MatchedArticle(article=article, links=[link],
                          classification=classify_article(article))


REAL_NOTICES = [
    ("HDB INVESTOR DEADLINE: HDFC Bank Limited Investors with Substantial Losses", 18),
    ("HDFC BANK LIMITED (HDB) SHAREHOLDER ALERT Bernstein", 20),
    ("HDFC Bank Limited (HDB) Lawsuit - Investors Urged to Contact Levi & Korsinsky", 24),
    ("DEADLINE ALERT for UWMC, HDB, SMPL and AARD: The Law Offices of Frank R. Cruz", 23),
    ("HDFC BANK LAWSUIT ALERT: Bragar Eagel & Squire, P.C.", 29),
    ("HDFC Bank Stock Update: Share Price Slips 1.24% on Lawsuit Reminders", 30),
]


def test_one_action_becomes_one_event():
    from src.event_cluster import cluster_articles

    clusters = cluster_articles(
        [_notice(t, d) for t, d in REAL_NOTICES], threshold=0.62, window_days=3
    )

    assert len(clusters) == 1, [c.articles[0].article.title for c in clusters]
    assert len(clusters[0].articles) == len(REAL_NOTICES)


def test_a_twelve_day_notice_period_is_still_one_proceeding():
    """The ordinary three-day window split the real set at its one gap."""
    from src.event_cluster import cluster_articles

    clusters = cluster_articles(
        [_notice("HDB SHAREHOLDER ALERT: HDFC Bank Limited Securities Class Action", 18),
         _notice("HDFC Bank Limited Class Action Reminder - Robbins LLP", 30)],
        threshold=0.62, window_days=3,
    )

    assert len(clusters) == 1


def test_notices_quoting_different_deadlines_stay_apart():
    """Two deadlines means two actions, however alike the headlines read."""
    from src.event_cluster import cluster_articles, same_proceeding

    first = _notice("HDFC Bank Limited Class Action - Contact Us Before October 13, 2026", 18)
    second = _notice("HDFC Bank Limited Class Action - Contact Us Before December 1, 2026", 19)

    assert not same_proceeding(first, second)
    assert len(cluster_articles([first, second], threshold=0.62, window_days=3)) == 2


def test_notices_about_different_companies_stay_apart():
    from src.event_cluster import same_proceeding

    assert not same_proceeding(
        _notice("HDFC Bank Limited Securities Class Action Lawsuit", ticker="HDFCBANK"),
        _notice("Coal India Limited Securities Class Action Lawsuit", ticker="COALINDIA"),
    )


def test_ordinary_litigation_news_is_not_swept_into_the_notice_rule():
    """A court ruling is a development; a plaintiff-firm advert is not.

    These must not merge just because both concern the same company and
    mention a court.
    """
    from src.event_cluster import same_proceeding

    ruling = _notice("Supreme Court limits forensic audit in the Daiichi dispute", 18)
    mou = _notice("HDFC Bank announces Q2 results date of October 17", 19)

    assert not same_proceeding(ruling, mou)
    assert not ruling.classification.legal_notice
    assert not mou.classification.legal_notice


def test_the_rule_needs_a_direct_mention_not_a_passing_one():
    """A notice that merely lists a ticker among others still needs the
    company to be the matched subject, which the DIRECT gate enforces."""
    from src.event_cluster import same_proceeding
    from src.models import Relationship

    a = _notice("HDFC Bank Limited Securities Class Action Lawsuit", 18)
    b = _notice("HDB Shareholder Alert: Securities Class Action", 19)
    b.links[0].relationship = Relationship.SECTOR  # no longer a direct mention

    assert not same_proceeding(a, b)
