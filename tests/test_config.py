"""Configuration loading and source quality."""

from __future__ import annotations

import pytest

from src.config import load_config
from src.models import SourceType


def test_config_loads(config):
    assert config.lookback_hours > 0
    assert config.max_queries_per_ticker > 0
    assert config.source_quality


def test_dotted_access(config):
    assert config.get("scoring.high_impact_threshold") == 8
    assert config.get("nothing.here", "fallback") == "fallback"


def test_overrides_merge_deeply():
    config = load_config(overrides={"scoring": {"report_min_impact": 9}})
    assert config.get("scoring.report_min_impact") == 9
    # untouched siblings survive
    assert config.get("scoring.high_impact_threshold") == 8


def test_source_quality_follows_the_specification(config):
    quality = config.source_quality
    assert quality["company_exchange_filing"] == 10
    assert quality["regulator"] == 10
    assert quality["company_ir"] == 9
    assert quality["reuters"] == 9
    assert quality["blog"] == 2
    assert quality["social_media"] == 1


def test_source_ranking_is_ordered(config):
    quality = config.source_quality
    assert quality["company_exchange_filing"] > quality["major_financial_press"]
    assert quality["major_financial_press"] > quality["established_newspaper"]
    assert quality["established_newspaper"] > quality["specialized_trade_publication"]
    assert quality["specialized_trade_publication"] > quality["unknown_news_site"]
    assert quality["unknown_news_site"] > quality["blog"] > quality["social_media"]


@pytest.mark.parametrize(
    "domain,expected",
    [
        ("www.reuters.com", SourceType.REUTERS),
        ("economictimes.indiatimes.com", SourceType.MAJOR_FINANCIAL_PRESS),
        ("www.nseindia.com", SourceType.COMPANY_EXCHANGE_FILING),
        ("rbi.org.in", SourceType.REGULATOR),
        ("www.pv-magazine.com", SourceType.SPECIALIZED_TRADE_PUBLICATION),
        ("some-random-site.test", SourceType.UNKNOWN_NEWS_SITE),
    ],
)
def test_domains_map_to_source_types(config, domain, expected):
    assert config.source_type_for_domain(domain) == expected


def test_feeds_are_filtered_by_ticker(config):
    solar = config.feeds(["WAAREEENER"])
    names = {f.name for f in solar}
    assert any("PV Magazine" in n for n in names)
    assert not any("ETBFSI" in n for n in names)


def test_ai_is_disabled_in_the_shipped_config(config):
    assert config.ai_enabled is False


def test_alerts_are_disabled_in_the_shipped_config(config):
    assert config.get("alerts.enabled") is False


def test_no_secret_is_stored_in_config(config):
    """Credentials belong in the environment, never in a committed file."""
    import json

    blob = json.dumps(config.raw).lower()
    for marker in ("api_key", "apikey", "token", "password", "secret"):
        assert marker not in blob
