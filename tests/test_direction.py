"""Direction analysis — deliberately never forced."""

from __future__ import annotations

import pytest

from src.classify import classify_article
from src.direction import analyse_direction
from src.entity_match import match_entity
from src.exposure_match import match_exposures
from src.models import Article, Direction
from src.relationship import determine_relationship


def direction_for(watchlist, headline, ticker):
    article = Article(title=headline, url="https://a.test/1")
    profile = watchlist.get(ticker)
    entity, _ = match_entity(article, profile)
    exposure = match_exposures(article, profile)
    classification = classify_article(article)
    link = determine_relationship(
        article, profile, entity, exposure if exposure.matches else None, classification
    )
    assert link is not None, f"no link for {headline!r} / {ticker}"
    return analyse_direction(headline, "", link, classification, profile)


@pytest.mark.parametrize(
    "headline,ticker,expected",
    [
        # An input cost going up is bad; a selling price going up is good.
        ("Polysilicon prices surge 30% on Chinese supply cuts", "WAAREEENER", Direction.NEGATIVE),
        ("Solar module prices fall 20% as Chinese oversupply deepens", "WAAREEENER",
         Direction.NEGATIVE),
        ("Newcastle thermal coal prices collapse to a four-year low", "COALINDIA",
         Direction.NEGATIVE),
        ("Newcastle thermal coal prices surge to a four-year high", "COALINDIA",
         Direction.POSITIVE),
    ],
)
def test_commodity_polarity_depends_on_the_company_role(watchlist, headline, ticker, expected):
    assert direction_for(watchlist, headline, ticker)[0] == expected


def test_a_competitors_win_is_negative_for_us(watchlist):
    direction, reasons = direction_for(
        watchlist, "Premier Energies wins a 2 GW module supply contract in the US", "WAAREEENER"
    )
    assert direction == Direction.NEGATIVE
    assert any("competitor" in r.lower() for r in reasons)


def test_a_big_factory_is_high_impact_but_uncertain(watchlist):
    """The specification's own example: growth against capital intensity."""
    direction, reasons = direction_for(
        watchlist, "Waaree Energies announces a Rs 3,000 crore new cell factory", "WAAREEENER"
    )
    assert direction == Direction.UNCERTAIN
    assert reasons


def test_rate_moves_are_mixed_for_a_lender(watchlist):
    direction, _ = direction_for(watchlist, "RBI cuts the repo rate by 25 bps", "HDFCBANK")
    assert direction == Direction.MIXED


def test_adverse_regulatory_action_is_negative(watchlist):
    direction, _ = direction_for(
        watchlist, "Supriya Lifescience receives a USFDA warning letter", "SUPRIYA"
    )
    assert direction == Direction.NEGATIVE


def test_an_order_win_is_positive(watchlist):
    direction, _ = direction_for(
        watchlist, "Freshara Agro bags an export order from a Spanish distributor", "FRESHARA"
    )
    assert direction == Direction.POSITIVE


def test_mixed_direction_headlines_use_the_nearer_word(watchlist):
    """'prices surge on supply cuts' contains both directions."""
    direction, _ = direction_for(
        watchlist, "Polysilicon prices surge 30% on Chinese supply cuts", "WAAREEENER"
    )
    assert direction == Direction.NEGATIVE


def test_no_signal_gives_uncertain_or_neutral(watchlist):
    direction, reasons = direction_for(
        watchlist, "Coal India publishes its sustainability report", "COALINDIA"
    )
    assert direction in {Direction.UNCERTAIN, Direction.NEUTRAL}
    assert reasons


def test_direction_never_encodes_a_recommendation(watchlist):
    for headline, ticker in [
        ("Waaree bags a 1.2 GW export order", "WAAREEENER"),
        ("Supriya Lifescience receives a USFDA warning letter", "SUPRIYA"),
    ]:
        _, reasons = direction_for(watchlist, headline, ticker)
        joined = " ".join(reasons).lower()
        for word in ("buy", "sell", "hold", "target price"):
            assert word not in joined
