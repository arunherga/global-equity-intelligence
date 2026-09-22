"""The DIRECT / INDIRECT_STRONG / INDIRECT / SECTOR / MACRO / WEAK ladder."""

from __future__ import annotations

import pytest

from src.classify import classify_article
from src.entity_match import match_entity
from src.exposure_match import match_exposures
from src.models import Relationship
from src.relationship import determine_relationship


def link_for(article, watchlist, ticker):
    profile = watchlist.get(ticker)
    entity, _ = match_entity(article, profile)
    exposure = match_exposures(article, profile)
    return determine_relationship(
        article, profile, entity, exposure if exposure.matches else None,
        classify_article(article),
    )


@pytest.mark.parametrize(
    "headline,ticker,expected",
    [
        # The specification's own worked examples.
        ("Coal India raises its production guidance for FY27", "COALINDIA", Relationship.DIRECT),
        ("China reduces solar export rebates; module prices to rise", "WAAREEENER",
         Relationship.INDIRECT_STRONG),
        ("US Federal Reserve changes interest rates", "HDFCBANK", Relationship.MACRO),
    ],
)
def test_specification_examples(watchlist, make_article, headline, ticker, expected):
    link = link_for(make_article(headline), watchlist, ticker)
    assert link is not None
    assert link.relationship == expected


def test_competitor_order_is_indirect_strong(watchlist, make_article):
    link = link_for(
        make_article("Premier Energies wins a 2 GW module supply contract in the United States"),
        watchlist, "WAAREEENER",
    )
    assert link.relationship == Relationship.INDIRECT_STRONG


def test_output_commodity_shock_is_indirect_strong(watchlist, make_article):
    link = link_for(
        make_article("Newcastle thermal coal prices collapse to a four-year low"),
        watchlist, "COALINDIA",
    )
    assert link.relationship == Relationship.INDIRECT_STRONG


def test_policy_in_an_export_market_is_indirect_strong(watchlist, make_article):
    link = link_for(
        make_article("EU tightens pesticide residue limits for imported vegetables from India"),
        watchlist, "FRESHARA",
    )
    assert link.relationship == Relationship.INDIRECT_STRONG


def test_sector_news_stays_sector(watchlist, make_article):
    link = link_for(
        make_article("India's rooftop solar installations grew last quarter"),
        watchlist, "WAAREEENER",
    )
    assert link.relationship in {Relationship.SECTOR, Relationship.INDIRECT}


def test_geography_alone_is_weak(watchlist, make_article):
    link = link_for(make_article("China opens a new high-speed rail line"), watchlist, "SUPRIYA")
    assert link is None or link.relationship == Relationship.WEAK


def test_nothing_without_the_company_reaches_direct(watchlist, make_article):
    """DIRECT is reserved for an article that names the company."""
    headlines = [
        "Chinese API plants shut after environmental inspection; API prices surge",
        "China solar module prices fall 20% as polysilicon glut deepens",
        "Container freight rates jump 18% on Red Sea disruption",
    ]
    for headline in headlines:
        for profile in watchlist.profiles:
            link = link_for(make_article(headline), watchlist, profile.ticker)
            if link is not None:
                assert link.relationship != Relationship.DIRECT, headline


def test_reasons_are_always_recorded(watchlist, make_article):
    link = link_for(
        make_article("Coal India raises its production guidance"), watchlist, "COALINDIA"
    )
    assert link.reasons and all(isinstance(r, str) for r in link.reasons)


def test_relationship_ordering():
    assert Relationship.DIRECT.stronger_than(Relationship.INDIRECT_STRONG)
    assert Relationship.INDIRECT_STRONG.stronger_than(Relationship.INDIRECT)
    assert Relationship.INDIRECT.stronger_than(Relationship.SECTOR)
    assert Relationship.SECTOR.stronger_than(Relationship.MACRO)
    assert Relationship.MACRO.stronger_than(Relationship.WEAK)
    assert Relationship.strongest(
        [Relationship.WEAK, Relationship.SECTOR, Relationship.DIRECT]
    ) is Relationship.DIRECT
