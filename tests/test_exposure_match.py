"""Exposure matching: news that never names the company."""

from __future__ import annotations

import pytest

from src.exposure_match import is_named_entity, match_all_exposures, match_exposures
from src.models import ExposureType


def exposures(article, watchlist, ticker):
    return match_exposures(article, watchlist.get(ticker))


def test_commodity_exposure_without_the_company_name(watchlist, make_article):
    article = make_article("China solar module prices fall 20% as polysilicon glut deepens")
    result = exposures(article, watchlist, "WAAREEENER")
    assert result.has(ExposureType.COMMODITY)
    assert "polysilicon" in result.terms_for(ExposureType.COMMODITY)


def test_competitor_exposure(watchlist, make_article):
    article = make_article("Premier Energies wins a 2 GW module supply contract in the US")
    result = exposures(article, watchlist, "WAAREEENER")
    assert "Premier Energies" in result.terms_for(ExposureType.COMPETITOR)


def test_supplier_exposure(watchlist, make_article):
    article = make_article("Salesforce increases AI investment for enterprise customers")
    result = exposures(article, watchlist, "KSOLVES")
    assert "Salesforce" in result.terms_for(ExposureType.SUPPLIER)


def test_customer_exposure(watchlist, make_article):
    article = make_article("NTPC floats a tender for 1 GW of solar capacity")
    result = exposures(article, watchlist, "WAAREEENER")
    assert "NTPC" in result.terms_for(ExposureType.CUSTOMER)


def test_regulator_exposure(watchlist, make_article):
    article = make_article("USFDA issues new guidance for imported drug substances")
    result = exposures(article, watchlist, "SUPRIYA")
    assert result.has(ExposureType.REGULATION)


def test_geographic_exposure_is_weak_on_its_own(watchlist, make_article):
    article = make_article("Spain reports record tourism numbers this summer")
    result = exposures(article, watchlist, "FRESHARA")
    assert result.has(ExposureType.GEOGRAPHY)
    # geography alone carries almost no weight
    assert result.total_weight < 2.0


def test_generic_geography_weighs_less_than_an_export_market(watchlist, make_article):
    article = make_article("India and Spain expand trade ties")
    result = exposures(article, watchlist, "FRESHARA")
    weights = {m.term.lower(): m.weight for m in result.matches}
    assert weights["spain"] > weights["india"]


def test_international_topics_are_flagged(watchlist, make_article):
    from src.exposure_match import INTERNATIONAL_DETAIL

    article = make_article("China reduces its solar export rebate")
    result = exposures(article, watchlist, "WAAREEENER")
    assert any(INTERNATIONAL_DETAIL in m.detail for m in result.matches)


def test_group_companies_are_not_the_company_itself(watchlist, make_article):
    article = make_article("HDFC Life Insurance quarterly profit rises 12%")
    result = exposures(article, watchlist, "HDFCBANK")
    subsidiary = [m for m in result.matches if m.exposure_type == ExposureType.SUBSIDIARY]
    assert subsidiary and subsidiary[0].relationship.value == "INDIRECT_STRONG"


def test_named_entity_detection():
    assert is_named_entity("Premier Energies")
    assert is_named_entity("NTPC")
    assert not is_named_entity("distributors")
    assert not is_named_entity("contract farmers")


def test_generic_roles_do_not_look_like_named_counterparties(watchlist, make_article):
    from src.exposure_match import GENERIC_ROLE_DETAIL

    article = make_article("Distributors report stronger demand this quarter")
    result = exposures(article, watchlist, "RAVEL")
    roles = [m for m in result.matches if GENERIC_ROLE_DETAIL in m.detail]
    assert roles, "a generic role should still match, but be marked as generic"
    assert all(m.weight <= 1.0 for m in roles)


def test_one_article_can_touch_several_companies(watchlist, make_article):
    article = make_article("RBI cuts the repo rate by 25 bps in its monetary policy review")
    results = match_all_exposures(article, watchlist.profiles)
    assert "TMB" in results and "HDFCBANK" in results


def test_irrelevant_news_produces_no_exposure(watchlist, make_article):
    article = make_article("Local school announces its annual sports day")
    assert match_all_exposures(article, watchlist.profiles) == {}
