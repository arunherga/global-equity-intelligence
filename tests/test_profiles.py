"""Watchlist loading, ticker normalisation and enrichment rules."""

from __future__ import annotations

import json

import pytest

from src.profiles.loader import AliasSpec, apply_enrichment, load_watchlist

EXPECTED = [
    "MSTCLTD", "WAAREEENER", "FRESHARA", "JKIPL", "RAVEL",
    "COALINDIA", "KSOLVES", "TMB", "SUPRIYA", "HDFCBANK",
]


def test_exactly_the_ten_configured_stocks_load(watchlist):
    assert watchlist.tickers == EXPECTED
    assert len(watchlist) == 10


def test_hdfc_normalises_to_hdfcbank(watchlist):
    assert watchlist.normalize("HDFC") == "HDFCBANK"
    assert watchlist.normalize("hdfc") == "HDFCBANK"
    assert watchlist.normalize("HDFC Bank") == "HDFCBANK"
    assert watchlist.get("HDFC").company == "HDFC Bank Limited"


def test_former_hdfc_limited_is_not_a_separate_equity(watchlist):
    """HDFC Limited merged into HDFC Bank; it must not exist as its own line."""
    assert "HDFCLTD" not in watchlist.tickers
    assert "HDFC" not in watchlist.tickers
    profile = watchlist.get("HDFCBANK")
    assert profile.company == "HDFC Bank Limited"


@pytest.mark.parametrize(
    "shorthand,expected",
    [
        ("MSTC", "MSTCLTD"),
        ("waaree", "WAAREEENER"),
        ("Coal India", "COALINDIA"),
        ("CIL", "COALINDIA"),
        ("jinkushal", "JKIPL"),
        ("Ravelcare", "RAVEL"),
        ("tamilnad", "TMB"),
        ("SupriyaLife", "SUPRIYA"),
    ],
)
def test_ticker_aliases(watchlist, shorthand, expected):
    assert watchlist.normalize(shorthand) == expected


def test_unknown_ticker_raises(watchlist):
    with pytest.raises(KeyError):
        watchlist.get("RELIANCE")


def test_select_preserves_watchlist_order(watchlist):
    chosen = watchlist.select(["SUPRIYA", "MSTCLTD", "HDFC"])
    assert [p.ticker for p in chosen] == ["MSTCLTD", "SUPRIYA", "HDFCBANK"]


def test_every_profile_has_the_intelligence_fields(watchlist):
    for profile in watchlist.profiles:
        assert profile.company
        assert profile.aliases, f"{profile.ticker} has no aliases"
        assert profile.sector_topics, f"{profile.ticker} has no sector topics"
        assert profile.international_topics, f"{profile.ticker} has no international topics"
        assert profile.regulators, f"{profile.ticker} has no regulators"


def test_exporters_declare_export_markets(watchlist):
    for ticker in ("WAAREEENER", "FRESHARA", "SUPRIYA", "JKIPL", "KSOLVES"):
        assert watchlist.get(ticker).export_markets, f"{ticker} should have export markets"


def test_commodity_roles_are_directional(watchlist):
    waaree = watchlist.get("WAAREEENER")
    assert waaree.commodity_role("polysilicon") == "input"
    assert waaree.commodity_role("solar module prices") == "output"
    coal = watchlist.get("COALINDIA")
    assert coal.commodity_role("thermal coal prices") == "output"
    assert coal.commodity_role("diesel prices") == "input"


def test_enrichment_adds_but_never_overwrites(watchlist):
    profile = watchlist.get("FRESHARA")
    original_products = list(profile.products)
    apply_enrichment(
        profile,
        {"fields": {"products": ["gherkins", "pickled okra"], "customers": ["A Fictional Buyer"]}},
    )
    # existing values survive, in their original order
    assert profile.products[: len(original_products)] == original_products
    # genuinely new values are appended
    assert "pickled okra" in profile.products
    assert "A Fictional Buyer" in profile.customers
    # and the addition is attributable
    assert "pickled okra" in profile.enriched_terms["products"]
    # a duplicate is not added twice
    assert profile.products.count("gherkins") == 1


def test_enrichment_is_recorded_separately(tmp_path, config, watchlist):
    from src.profiles.enrichment import Enricher

    enricher = Enricher(config)
    path = enricher.path_for("SUPRIYA")
    assert path.name == "SUPRIYA.enriched.json"
    assert "data" in str(path) and "profiles" in str(path)
    # enrichment never writes to watchlist.yaml
    assert path.suffix == ".json"


def test_alias_spec_parsing():
    plain = AliasSpec.parse("Waaree Energies")
    assert plain.value == "Waaree Energies" and plain.is_primary

    guarded = AliasSpec.parse(
        {"value": "Ravel", "strength": "secondary", "requires": ["haircare"], "excludes": ["Ravel Electronics"]}
    )
    assert guarded.strength == "secondary"
    assert guarded.requires == ("haircare",)
    assert guarded.excludes == ("ravel electronics",)
    assert AliasSpec.parse("") is None
