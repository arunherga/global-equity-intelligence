"""Event classification."""

from __future__ import annotations

import pytest

from src.classify import RuleClassifier, classify_article
from src.models import EventCategory as C


def categories(make_article, headline, **kwargs):
    return set(classify_article(make_article(headline, **kwargs)).categories)


@pytest.mark.parametrize(
    "headline,expected",
    [
        ("Waaree Energies bags a 1.2 GW export order from a US developer", C.ORDER_WIN),
        ("Waaree Energies bags a 1.2 GW export order from a US developer", C.EXPORT_ORDER),
        ("Ksolves India reports 28% revenue growth; net profit rises", C.EARNINGS),
        ("Coal India raises its FY27 production guidance", C.GUIDANCE),
        ("Coal India declares an interim dividend of Rs 5 per share", C.DIVIDEND),
        ("Supriya Lifescience receives a USFDA warning letter", C.REGULATORY),
        ("Newcastle thermal coal prices collapse to a four-year low", C.COMMODITY),
        ("RBI cuts the repo rate by 25 bps", C.INTEREST_RATE),
        ("US imposes anti-dumping duty on solar cell imports", C.TARIFF),
        ("Waaree commissions a new 5.4 GW cell plant in Gujarat", C.NEW_PLANT),
        ("Ksolves appoints a new CFO", C.MANAGEMENT_CHANGE),
        ("Container freight rates jump 18% on Red Sea disruption", C.SUPPLY_CHAIN),
        ("EU tightens pesticide residue limits for imported vegetables", C.REGULATORY),
        ("MSTC wins an e-auction mandate for coal block auctions", C.ORDER_WIN),
        ("Government notifies expanded vehicle scrappage policy incentives", C.GOVERNMENT_POLICY),
        ("Should you buy Coal India? Analysts see 20% upside", C.ANALYST_ACTION),
    ],
)
def test_headlines_land_in_the_right_category(make_article, headline, expected):
    assert expected in categories(make_article, headline)


def test_an_event_can_carry_several_categories(make_article):
    found = categories(make_article, "Waaree bags a 1.2 GW export order from a US developer")
    assert {C.ORDER_WIN, C.EXPORT_ORDER} <= found


def test_speculation_is_flagged_not_categorised_away(make_article):
    result = classify_article(
        make_article("Supriya Lifescience may consider a capacity expansion, sources say")
    )
    assert result.speculative
    assert C.CAPACITY_EXPANSION in result.categories


def test_opinion_pieces_are_flagged(make_article):
    result = classify_article(make_article("Should you buy Coal India? Analysts see 20% upside"))
    assert result.opinion


def test_official_language_is_detected(make_article):
    result = classify_article(
        make_article("Coal India: outcome of board meeting filed under Regulation 30")
    )
    assert result.official_language


def test_unclassifiable_news_is_other(make_article):
    result = classify_article(make_article("Mumbai weather update: heavy rain expected"))
    assert result.categories == [C.OTHER]


def test_classification_is_deterministic(make_article):
    article = make_article("Waaree Energies bags a 1.2 GW export order")
    first = classify_article(article).categories
    second = RuleClassifier().classify(article).categories
    assert first == second


def test_word_boundaries_apply_to_categories(make_article):
    """'cut' must not match 'executive', 'ban' must not match 'urban'."""
    result = classify_article(make_article("The executive team reviewed urban demand"))
    assert C.INTEREST_RATE not in result.categories
