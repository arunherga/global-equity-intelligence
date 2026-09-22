"""Automatic query generation."""

from __future__ import annotations

import pytest

from src.query_generator import QueryKind, flatten, generate_for_profile, generate_queries


def test_every_company_gets_queries(watchlist, config):
    queries = generate_queries(watchlist.profiles, budget=config.max_queries_per_ticker)
    assert set(queries) == set(watchlist.tickers)
    for ticker, items in queries.items():
        assert items, f"{ticker} generated no queries"
        assert len(items) <= config.max_queries_per_ticker


def test_queries_cover_more_than_the_company_name(watchlist):
    queries = generate_for_profile(watchlist.get("WAAREEENER"), budget=26)
    kinds = {q.kind for q in queries}
    assert QueryKind.COMPANY in kinds
    assert QueryKind.INTERNATIONAL in kinds
    assert QueryKind.COMMODITY in kinds
    assert QueryKind.SECTOR in kinds


def test_international_queries_exist_for_exporters(watchlist):
    for ticker in ("WAAREEENER", "FRESHARA", "SUPRIYA", "KSOLVES"):
        queries = generate_for_profile(watchlist.get(ticker), budget=26)
        assert any(q.international for q in queries), ticker


def test_specific_expected_queries_are_generated(watchlist):
    texts = [q.text.lower() for q in generate_for_profile(watchlist.get("WAAREEENER"), budget=30)]
    assert any("waaree energies" in t for t in texts)
    assert any("polysilicon" in t for t in texts)
    assert any("almm" in t for t in texts)

    texts = [q.text.lower() for q in generate_for_profile(watchlist.get("FRESHARA"), budget=30)]
    assert any("freshara agro" in t for t in texts)
    assert any("gherkin" in t for t in texts)
    assert any("pesticide" in t for t in texts)


def test_company_queries_are_quoted(watchlist):
    queries = generate_for_profile(watchlist.get("COALINDIA"), budget=26)
    company = [q for q in queries if q.kind == QueryKind.COMPANY]
    assert any(q.text.startswith('"') for q in company)


def test_the_short_name_comes_from_the_registered_name(watchlist):
    """'Waaree Energies', not 'Waaree Solar'."""
    queries = generate_for_profile(watchlist.get("WAAREEENER"), budget=26)
    modifiers = [q.text for q in queries if q.kind == QueryKind.COMPANY and " order" in q.text]
    assert modifiers
    assert all("Waaree Energies" in q for q in modifiers)


def test_generation_is_deterministic(watchlist):
    profile = watchlist.get("SUPRIYA")
    assert [q.text for q in generate_for_profile(profile, budget=20)] == [
        q.text for q in generate_for_profile(profile, budget=20)
    ]


def test_budget_is_respected(watchlist):
    assert len(generate_for_profile(watchlist.get("TMB"), budget=7)) == 7


def test_shared_queries_are_issued_once(watchlist):
    """Both banks want 'RBI monetary policy'; it should be searched once."""
    queries = generate_queries(watchlist.profiles, budget=26)
    flat = flatten(queries)
    texts = [q.text.lower() for q in flat]
    assert len(texts) == len(set(texts))


def test_every_query_knows_why_it_exists(watchlist):
    for query in flatten(generate_queries(watchlist.profiles, budget=10)):
        assert query.ticker
        assert query.term
        assert query.expected_relationship
        assert query.exposure_type
