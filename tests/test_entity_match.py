"""Company identification, including the look-alike names."""

from __future__ import annotations

import pytest

from src.entity_match import match_all_entities, match_entity


def tickers_for(article, watchlist):
    matches, _ = match_all_entities(article, watchlist.profiles)
    return sorted(m.ticker for m in matches)


def test_ravelcare_is_matched(watchlist, make_article):
    article = make_article("Ravelcare Limited launches a new haircare range")
    assert "RAVEL" in tickers_for(article, watchlist)


def test_ravel_electronics_is_not_ravelcare(watchlist, make_article):
    """Regression: two unrelated businesses share a name fragment."""
    article = make_article("Ravel Electronics bags an LED street-lighting order in Chennai")
    assert "RAVEL" not in tickers_for(article, watchlist)


def test_ravel_alone_needs_personal_care_context(watchlist, make_article):
    with_context = make_article("Ravel expands its skincare portfolio as personal care demand grows")
    without_context = make_article("Ravel wins a lighting contract from the city council")
    assert "RAVEL" in tickers_for(with_context, watchlist)
    assert "RAVEL" not in tickers_for(without_context, watchlist)


def test_maurice_ravel_is_not_a_holding(watchlist, make_article):
    article = make_article("Maurice Ravel's Bolero performed at the symphony")
    assert "RAVEL" not in tickers_for(article, watchlist)


def test_supriya_is_a_common_first_name(watchlist, make_article):
    person = make_article("Supriya Sule slams the government over farm policy")
    company = make_article("Supriya Lifescience receives a USFDA approval for a new API")
    assert "SUPRIYA" not in tickers_for(person, watchlist)
    assert "SUPRIYA" in tickers_for(company, watchlist)


def test_hdfc_life_is_not_hdfc_bank_direct_news(watchlist, make_article):
    """HDFC Life is a listed group entity, not HDFC Bank itself."""
    article = make_article("HDFC Life Insurance quarterly profit rises 12%")
    matches, _ = match_all_entities(article, watchlist.profiles)
    assert not any(m.ticker == "HDFCBANK" and m.is_primary for m in matches)


def test_hdfc_bank_itself_is_matched(watchlist, make_article):
    article = make_article("HDFC Bank reports strong deposit growth and stable NIM")
    matches, _ = match_all_entities(article, watchlist.profiles)
    hit = next(m for m in matches if m.ticker == "HDFCBANK")
    assert hit.is_primary and hit.in_headline


def test_subsidiary_counts_as_the_parent(watchlist, make_article):
    article = make_article("Mahanadi Coalfields commissions a new washery in Odisha")
    matches, _ = match_all_entities(article, watchlist.profiles)
    hit = next(m for m in matches if m.ticker == "COALINDIA")
    assert hit.strength == "subsidiary"


def test_tmb_abbreviation_needs_banking_context(watchlist, make_article):
    bank = make_article("TMB reports higher deposit growth; RBI nod awaited for branch expansion")
    other = make_article("TMB Thailand announces a rebranding")
    assert "TMB" in tickers_for(bank, watchlist)
    assert "TMB" not in tickers_for(other, watchlist)


def test_rejections_are_recorded_for_explainability(watchlist, make_article):
    article = make_article("Ravel Electronics bags an LED order")
    profile = watchlist.get("RAVEL")
    match, rejections = match_entity(article, profile)
    assert match is None
    assert any("excluded phrase" in r.reason for r in rejections)


def test_collector_ticker_hints_are_trusted(watchlist, make_article):
    article = make_article("Intimation under Regulation 30")
    article.tickers_hint = ["JKIPL"]
    assert "JKIPL" in tickers_for(article, watchlist)


def test_unrelated_news_matches_nothing(watchlist, make_article):
    for headline in (
        "Indian cricket team wins the series in Australia",
        "Mumbai weather update: heavy rain expected",
    ):
        assert tickers_for(make_article(headline), watchlist) == []
