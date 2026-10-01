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


# -- shapes found in the 2026-09-30 report --------------------------------
#
# Every title here is real, and every one of them scored 8/15 or higher.

import pytest as _pytest


def _flags(title):
    from src.classify import classify_article
    from src.models import Article

    return classify_article(
        Article(title=title, url="https://example.test/a", source_domain="example.test")
    )


@_pytest.mark.parametrize("title", [
    "Digital Transformation Market Report 2026: Capitalize on the $2.47 Trillion Revenue Surge",
    "Next Generation Computing Market Report 2026: Capitalize on the $486 Billion Revenue",
    "IT BFSI Market Report 2026: Capitalize on the $171.25 Billion Revenue Surge",
    "Ore Cars and Parts Market Forecast to 2035: Growth Momentum Builds on Fleet Renewals",
])
def test_syndicated_research_advertisements_are_flagged(title):
    """The highest-scoring item on 2026-09-30 was one of these, at 14/15.

    It was read as a severe supply-chain disruption because it mentioned a
    supplier in passing.
    """
    assert _flags(title).research_report is True


@_pytest.mark.parametrize("title", [
    "ABBOTT INDIA Stock/Share price , NSE/BSE Forecast News and Live Quotes",
    "HIMALAYA NUTRAVEDICS INDIA LTD. Stock/Share price , NSE/BSE Forecast News and Live Quotes",
    "HDFA Forecast — Price Target — Prediction for 2027",
    "Top 20 Energy Stocks To Buy In India For September 2026 | Best Long-Term Energy Stocks",
])
def test_quote_pages_and_tip_lists_are_flagged(title):
    assert _flags(title).listing_page is True


@_pytest.mark.parametrize("title", [
    "Coal India and HURL Sign MoU to Explore Coal Gasification-Based Urea Plant",
    "HDFC Bank Limited (HDB) Investors: Securities Fraud Class Action Filed",
    "Waaree Energies Subsidiary WCES Forays Into Specialty Gases For Semiconductor",
    "SAIL, BCCL sign MoU to jointly develop two West Bengal coal blocks",
    "Coal India announces senior management change as Dr. Anjani Kumar steps down",
    "U.S. Solar Module Prices Jump 40% Before Import Curbs Take Effect",
])
def test_real_developments_are_not_flagged(title):
    """The penalties are worthless if they also hit the news worth having."""
    flags = _flags(title)

    assert not flags.research_report, title
    assert not flags.listing_page, title
    assert not flags.recruitment, title
    assert not flags.routine_release, title
