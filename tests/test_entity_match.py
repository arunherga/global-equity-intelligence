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


def test_an_exchange_collectors_hint_is_trusted(watchlist, make_article):
    """NSE, BSE and IR pages are fetched per scrip code.

    An item returned by Jinkushal's filing feed is about Jinkushal, even
    when the title is boilerplate that names nobody.
    """
    article = make_article("Intimation under Regulation 30")
    article.tickers_hint = ["JKIPL"]
    article.collector = "bse"

    assert "JKIPL" in tickers_for(article, watchlist)


def test_an_official_filing_hint_is_trusted(watchlist, make_article):
    article = make_article("Outcome of board meeting")
    article.tickers_hint = ["JKIPL"]
    article.is_official = True

    assert "JKIPL" in tickers_for(article, watchlist)


def test_a_search_collectors_hint_is_not_evidence(watchlist, make_article):
    """The worst precision bug this system had.

    Google News tags each result with the query that found it. Treating that
    as a confirmed headline-strength mention meant a Coal India query
    returning "thyssenkrupp nucera wins chlor-alkali order from Hongniu
    Lanzhou in China" scored 10/15 as a direct Coal India event, and an HDFC
    Bank query returning a Power Mech Projects order scored 9/15 - on
    2026-09-30, both real.
    """
    article = make_article(
        "thyssenkrupp nucera wins chlor-alkali order from Hongniu Lanzhou in China"
    )
    article.tickers_hint = ["COALINDIA"]
    article.collector = "google_news"

    assert "COALINDIA" not in tickers_for(article, watchlist)


def test_a_search_hint_does_not_bypass_the_negative_alias_guards(watchlist, make_article):
    """It returned before every guard built to stop exactly this."""
    article = make_article("Ravel Electronics wins a municipal lighting contract")
    article.tickers_hint = ["RAVEL"]
    article.collector = "google_news"

    assert "RAVEL" not in tickers_for(article, watchlist)


def test_the_hint_still_cannot_invent_a_match_the_text_supports(watchlist, make_article):
    """A trusted hint for a company the text does name is still fine."""
    article = make_article("Jinkushal Industries wins a repeat order")
    article.tickers_hint = ["JKIPL"]
    article.collector = "google_news"

    assert "JKIPL" in tickers_for(article, watchlist), "matched on the name, not the hint"


def test_unrelated_news_matches_nothing(watchlist, make_article):
    for headline in (
        "Indian cricket team wins the series in Australia",
        "Mumbai weather update: heavy rain expected",
    ):
        assert tickers_for(make_article(headline), watchlist) == []


# -- brands and foreign subsidiaries --------------------------------------
#
# A company is often reported under a name that is not its listed name. Trade
# press writes about HexL, not Jinkushal; Spanish press writes about
# Aceitunas Sarasa, not Freshara. Brands were previously used only to build
# queries, so those articles were fetched and then dropped for matching
# nothing - the worst possible outcome, since the collection cost was paid.


def _match(watchlist, ticker, title, summary=""):
    from src.matching import fold
    from src.models import Article

    article = Article(
        title=title, summary=summary,
        url="https://example.test/a", source_domain="example.test",
    )
    return match_entity(
        article, watchlist.get(ticker), fold(article.text), fold(article.title)
    )[0]


def test_a_brand_alone_identifies_the_company(watchlist):
    """"HexL wins an order" names Jinkushal nowhere."""
    match = _match(watchlist, "JKIPL", "HexL backhoe loaders enter the Ghanaian market")

    assert match is not None
    assert match.strength == "brand"
    assert match.alias == "HexL"
    assert match.is_primary, "a brand is the name it trades under, not a hedge"


def test_a_brand_match_is_case_insensitive(watchlist):
    for spelling in ("HexL", "HEXL", "hexl"):
        match = _match(watchlist, "JKIPL", f"{spelling} loaders win a repeat order")
        assert match is not None, spelling


def test_the_spanish_subsidiary_identifies_freshara(watchlist):
    match = _match(
        watchlist, "FRESHARA", "El grupo indio compra Aceitunas Sarasa, de Navarra"
    )

    assert match is not None
    assert match.strength == "subsidiary"
    assert "Aceitunas Sarasa" in match.alias


def test_the_surname_sarasa_alone_is_not_freshara(watchlist):
    """Sarasa is a common Navarrese surname and place name.

    Listing it bare would have matched local politics, sport and obituaries.
    """
    assert _match(
        watchlist, "FRESHARA", "Sarasa appointed to the state agriculture board"
    ) is None
    assert _match(watchlist, "FRESHARA", "Miguel Sarasa wins the Navarre stage") is None


def test_the_ravel_pro_range_identifies_ravelcare(watchlist):
    match = _match(
        watchlist, "RAVEL", "RavelPRO Zero Hairfall Shampoo reviewed by dermatologists"
    )

    assert match is not None
    assert match.strength == "brand"


def test_brands_did_not_weaken_the_existing_ravel_guards(watchlist):
    """The new brand path must not reopen what the alias guards closed."""
    assert _match(
        watchlist, "RAVEL", "Ravel Electronics wins a municipal lighting contract"
    ) is None
    assert _match(watchlist, "RAVEL", "Maurice Ravel's Bolero performed in Chennai") is None


def test_every_brand_and_subsidiary_is_distinctive_enough(watchlist):
    """A one-word lowercase brand would match half the news in the world.

    Cheap structural guard: names added later get the same scrutiny these
    did, without anyone having to remember why.
    """
    too_generic = {
        "pro", "care", "zero", "fresh", "hex", "plus", "max", "one", "prime",
        "shampoo", "serum", "oil", "loader", "olives",
    }
    for profile in watchlist:
        for name in list(profile.brands) + list(profile.subsidiaries):
            assert len(name) >= 4, f"{profile.ticker}: {name!r} is too short"
            assert name.lower() not in too_generic, f"{profile.ticker}: {name!r}"
            assert any(c.isupper() for c in name), (
                f"{profile.ticker}: {name!r} must be capitalised - lower case "
                "marks a generic term, and these are matched as named entities"
            )
