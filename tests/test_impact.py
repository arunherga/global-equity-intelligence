"""Impact scoring, confidence and source ranking."""

from __future__ import annotations

import pytest

from src.classify import Classification, classify_article
from src.impact import (
    ScoreInput,
    apply_relationship_caps,
    business_impacts,
    has_magnitude,
    score_confidence,
    score_impact,
    time_horizon,
    watch_next_items,
)
from src.models import (
    IMPACT_SCORE_MAX,
    BusinessImpact,
    Event,
    EventCategory,
    Relationship,
    SourceType,
    TimeHorizon,
    impact_band,
)
from src.relationship import StockLink


def make_input(watchlist, headline, ticker, relationship, source_types=(), sources=1, **kw):
    from src.entity_match import match_entity
    from src.exposure_match import match_exposures
    from src.models import Article

    article = Article(title=headline, url="https://a.test/1")
    profile = watchlist.get(ticker)
    entity, _ = match_entity(article, profile)
    exposure = match_exposures(article, profile)
    link = StockLink(
        ticker=ticker,
        relationship=relationship,
        entity=entity,
        exposures=exposure.matches,
        evidence_weight=exposure.total_weight,
        **kw,
    )
    event = Event(event_id="EVENT-X-2026-0001", title=headline, event_date=None)
    return ScoreInput(
        event=event,
        link=link,
        classification=classify_article(article),
        profile=profile,
        headline=headline,
        source_types=tuple(source_types) or (SourceType.UNKNOWN_NEWS_SITE,),
        independent_sources=sources,
    )


def test_scores_stay_inside_the_band(watchlist):
    data = make_input(
        watchlist,
        "Waaree Energies bags a record 1.2 GW export order worth Rs 4,200 crore",
        "WAAREEENER", Relationship.DIRECT,
        source_types=[SourceType.COMPANY_EXCHANGE_FILING, SourceType.REUTERS], sources=4,
    )
    score, reasons = score_impact(data)
    assert 0 <= score <= IMPACT_SCORE_MAX
    assert reasons


def test_every_score_is_explained(watchlist):
    data = make_input(watchlist, "Coal India raises production guidance", "COALINDIA",
                      Relationship.DIRECT)
    score, reasons = score_impact(data)
    assert reasons, "a score without reasons is not acceptable"
    assert all(r[0] in "+-" or "capped" in r for r in reasons)


def test_a_direct_event_outscores_the_same_news_seen_indirectly(watchlist):
    direct = make_input(watchlist, "Coal India raises production guidance", "COALINDIA",
                        Relationship.DIRECT)
    indirect = make_input(watchlist, "Coal India raises production guidance", "MSTCLTD",
                          Relationship.INDIRECT_STRONG)
    assert score_impact(direct)[0] > score_impact(indirect)[0]


def test_opinion_pieces_are_pushed_down(watchlist):
    data = make_input(watchlist, "Should you buy Coal India? Analysts see 20% upside",
                      "COALINDIA", Relationship.DIRECT)
    score, reasons = score_impact(data)
    assert any("Opinion" in r for r in reasons)
    assert score < 8


def test_speculation_is_penalised(watchlist):
    firm = make_input(watchlist, "Supriya Lifescience board approves a capacity expansion",
                      "SUPRIYA", Relationship.DIRECT)
    rumour = make_input(watchlist, "Supriya Lifescience may consider a capacity expansion, "
                                   "sources say", "SUPRIYA", Relationship.DIRECT)
    assert score_impact(firm)[0] > score_impact(rumour)[0]


def test_official_filings_earn_points(watchlist):
    plain = make_input(watchlist, "Coal India declares an interim dividend", "COALINDIA",
                       Relationship.DIRECT, source_types=[SourceType.UNKNOWN_NEWS_SITE])
    filed = make_input(watchlist, "Coal India declares an interim dividend", "COALINDIA",
                       Relationship.DIRECT, source_types=[SourceType.COMPANY_EXCHANGE_FILING])
    assert score_impact(filed)[0] > score_impact(plain)[0]


def test_several_independent_sources_earn_points(watchlist):
    one = make_input(watchlist, "Waaree bags a 1.2 GW export order", "WAAREEENER",
                     Relationship.DIRECT, sources=1)
    many = make_input(watchlist, "Waaree bags a 1.2 GW export order", "WAAREEENER",
                      Relationship.DIRECT, sources=4)
    assert score_impact(many)[0] > score_impact(one)[0]


def test_macro_and_sector_links_are_capped(watchlist):
    reasons = []
    assert apply_relationship_caps(14, Relationship.MACRO, {"macro_relationship_cap": 8}, reasons) == 8
    assert any("capped" in r for r in reasons)
    assert apply_relationship_caps(
        14, Relationship.SECTOR, {"sector_relationship_cap": 9}, []
    ) == 9
    assert apply_relationship_caps(6, Relationship.DIRECT, {}, []) == 6


def test_impact_bands_follow_the_specification():
    assert impact_band(1) == "NOISE"
    assert impact_band(4) == "LOW"
    assert impact_band(6) == "RELEVANT"
    assert impact_band(9) == "HIGH"
    assert impact_band(12) == "VERY_HIGH"
    assert impact_band(14) == "CRITICAL"


def test_magnitude_detection():
    assert has_magnitude("order worth Rs 4,200 crore")
    assert has_magnitude("prices fall 20%")
    assert has_magnitude("prices collapse to a record low")
    assert not has_magnitude("company comments on market conditions")


def test_confidence_rises_with_source_quality(watchlist, config):
    weak = make_input(watchlist, "Waaree bags an order", "WAAREEENER", Relationship.DIRECT,
                      source_types=[SourceType.BLOG])
    strong = make_input(watchlist, "Waaree bags an order", "WAAREEENER", Relationship.DIRECT,
                        source_types=[SourceType.COMPANY_EXCHANGE_FILING], sources=3)
    assert score_confidence(strong, config.source_quality)[0] > score_confidence(
        weak, config.source_quality
    )[0]


def test_confidence_is_never_certain(watchlist, config):
    data = make_input(watchlist, "Waaree bags a record Rs 4,200 crore order", "WAAREEENER",
                      Relationship.DIRECT,
                      source_types=[SourceType.COMPANY_EXCHANGE_FILING, SourceType.REUTERS],
                      sources=6)
    confidence, reasons = score_confidence(data, config.source_quality)
    assert 0.0 < confidence <= 0.95
    assert reasons


def test_weak_relationships_lower_confidence(watchlist, config):
    direct = make_input(watchlist, "Coal India output rises", "COALINDIA", Relationship.DIRECT)
    weak = make_input(watchlist, "Coal India output rises", "COALINDIA", Relationship.WEAK)
    assert score_confidence(direct, config.source_quality)[0] > score_confidence(
        weak, config.source_quality
    )[0]


def test_business_impacts_reflect_the_commodity_role(watchlist):
    profile = watchlist.get("WAAREEENER")
    from src.exposure_match import match_exposures
    from src.models import Article

    article = Article(title="Polysilicon prices surge 30%", url="https://a.test/1")
    exposure = match_exposures(article, profile)
    link = StockLink(ticker="WAAREEENER", relationship=Relationship.INDIRECT_STRONG,
                     exposures=exposure.matches)
    impacts = business_impacts([EventCategory.COMMODITY], link, profile)
    assert BusinessImpact.INPUT_COST in impacts


def test_time_horizons():
    assert time_horizon([EventCategory.EARNINGS], Relationship.DIRECT) == TimeHorizon.IMMEDIATE
    assert time_horizon([EventCategory.NEW_PLANT], Relationship.DIRECT) == TimeHorizon.LONG_TERM
    assert time_horizon([EventCategory.COMMODITY], Relationship.DIRECT) == TimeHorizon.SHORT_TERM
    assert time_horizon([], Relationship.DIRECT) == TimeHorizon.UNKNOWN
    # a macro link does not bite the same day
    assert time_horizon([EventCategory.EARNINGS], Relationship.MACRO) == TimeHorizon.SHORT_TERM


def test_watch_next_is_concrete():
    link = StockLink(ticker="WAAREEENER", relationship=Relationship.DIRECT)
    items = watch_next_items([EventCategory.EXPORT_ORDER], link)
    assert items
    assert all(isinstance(i, str) and i for i in items)


# -- routine noise ---------------------------------------------------------


@pytest.mark.parametrize(
    "headline",
    [
        "RBI to conduct Overnight Variable Rate Reverse Repo (VRRR) auction under LAF",
        "Money Market Operations as on September 21, 2026",
        "Supervisory Data Quality Index for Scheduled Commercial Banks (June 2026)",
    ],
)
def test_scheduled_statistical_releases_are_pushed_down(watchlist, headline):
    """A regulator feed is high quality, which is why these used to score 8-10."""
    data = make_input(watchlist, headline, "HDFCBANK", Relationship.INDIRECT,
                      source_types=[SourceType.REGULATOR])
    score, reasons = score_impact(data)
    assert any("statistical release" in r for r in reasons)
    assert score < 5, f"{headline!r} still scored {score}"


@pytest.mark.parametrize(
    "headline",
    [
        "Stock Market Next Week: US-Iran tensions and crude oil to keep Sensex on edge",
        "Sensex, Nifty snap 4-day rally as IT and PSU banks drag",
    ],
)
def test_market_commentary_is_pushed_down(watchlist, headline):
    data = make_input(watchlist, headline, "HDFCBANK", Relationship.MACRO)
    score, reasons = score_impact(data)
    assert any("Market commentary" in r for r in reasons)
    assert score < 5


def test_real_policy_news_is_not_caught_by_the_noise_filters(watchlist):
    """The filters must not swallow an actual rate decision."""
    from src.classify import classify_article
    from src.models import Article

    result = classify_article(
        Article(title="RBI cuts repo rate by 25 bps in monetary policy review",
                url="https://x.test/1"))
    assert not result.routine_release
    assert not result.market_chatter


# -- event-level flags ----------------------------------------------------
#
# An 18-article cluster about an RBI overnight liquidity auction scored 10/15
# on 2026-09-29. "variable rate reverse repo" was already in the routine
# markers and the classifier did set the flag - but scoring used the
# classification of whichever single article won on relationship strength,
# and that one headline said only "RBI absorbs Rs 71,971 crore liquidity
# from banks". The penalty never applied.


def _matched(title, links=()):
    from src.classify import classify_article
    from src.event_cluster import MatchedArticle
    from src.models import Article

    article = Article(title=title, url=f"https://x.test/{abs(hash(title))}",
                      source_domain="x.test")
    return MatchedArticle(
        article=article, links=list(links), classification=classify_article(article)
    )


def test_cluster_flags_follow_the_majority():
    from src.main import _cluster_flags

    class _Cluster:
        articles = [
            _matched("RBI to conduct Overnight Variable Rate Reverse Repo (VRRR) auction"),
            _matched("Money Market Operations as on September 21, 2026"),
            _matched("RBI absorbs Rs 71,971 cr via overnight VRRR auction"),
            _matched("RBI absorbs Rs 71,971 crore liquidity from banks"),
        ]

    assert _cluster_flags(_Cluster())["routine_release"] is True


def test_one_routine_item_does_not_suppress_a_real_story():
    """Majority, not any: a wire snippet inside a real story must not bury it."""
    from src.main import _cluster_flags

    class _Cluster:
        articles = [
            _matched("Coal India and HURL sign MoU for a urea plant"),
            _matched("Coal India signs non-binding MoU with Hindustan Urvarak"),
            _matched("Coal India, HURL to explore coal gasification"),
            _matched("Money Market Operations as on September 21, 2026"),
        ]

    assert _cluster_flags(_Cluster())["routine_release"] is False


def test_an_empty_cluster_produces_no_flags():
    from src.main import _cluster_flags

    class _Cluster:
        articles = []

    assert _cluster_flags(_Cluster()) == {}


# -- the new penalties ----------------------------------------------------


def _score(title, relationship=None, exposures=(), ticker="TMB"):
    from src.classify import classify_article
    from src.impact import ScoreInput, score_impact
    from datetime import date
    from src.models import Article, Event, Relationship
    from src.relationship import StockLink

    from src.profiles import load_watchlist

    profile = load_watchlist().get(ticker)
    article = Article(title=title, url="https://x.test/a", source_domain="x.test")
    link = StockLink(
        ticker=ticker,
        relationship=relationship or Relationship.DIRECT,
        entity=None, exposures=list(exposures), reasons=[], evidence_weight=4.0,
    )
    data = ScoreInput(
        event=Event(event_id="E-1", title=title, event_date=date(2026, 9, 29)),
        link=link, classification=classify_article(article),
        profile=profile, text="", headline=title,
        source_types=(), independent_sources=1,
    )
    return score_impact(data)


def test_a_recruitment_notice_is_penalised():
    """"TMB Product Specialist Recruitment 2026 - Apply Online" scored 9/15."""
    score, reasons = _score("TMB Product Specialist Recruitment 2026 - Apply Online")

    assert any("Recruitment" in r for r in reasons)
    assert score < 5, f"must fall below the report threshold, got {score}"


def test_a_share_price_listing_page_is_penalised():
    score, reasons = _score("REGAAL Share Price Update: Rs 99.15, Market Cap Rs 861.34 Cr")

    assert any("listing" in r.lower() for r in reasons)
    assert score < 5, f"must fall below the report threshold, got {score}"


def test_a_real_development_keeps_its_score():
    """The penalties must not touch genuine company news."""
    score, reasons = _score(
        "Coal India and HURL sign MoU to explore a coal gasification urea plant",
        ticker="COALINDIA",
    )

    assert not any("listing" in r.lower() or "Recruitment" in r for r in reasons)
    assert score >= 5
