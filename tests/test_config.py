"""Configuration loading and source quality."""

from __future__ import annotations

import re

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


# Model names are provider-specific and a mismatch is the easiest config
# mistake to make: switch `provider` to gemini, leave `model` at the Ollama
# tag, and every call fails with an unhelpful 404. Ollama tags carry a colon
# (llama3.1:8b); no hosted provider's model name does.
_MODEL_SHAPES = {
    "ollama": lambda m: ":" in m,
    "gemini": lambda m: m.startswith("gemini-"),
    "openai": lambda m: m.startswith(("gpt-", "o1", "o3", "o4")),
    "anthropic": lambda m: m.startswith("claude-"),
}


def test_the_shipped_ai_provider_and_model_agree(config):
    provider = str(config.get("ai.provider", "")).lower()
    model = str(config.get("ai.model", ""))

    assert provider in _MODEL_SHAPES, f"unknown provider {provider!r}"
    assert _MODEL_SHAPES[provider](model), (
        f"ai.model {model!r} does not look like a {provider} model - "
        "switching provider means switching model too"
    )


def test_the_model_shape_check_would_catch_a_mismatch():
    """A guard that cannot fail is not a guard."""
    assert not _MODEL_SHAPES["gemini"]("llama3.1:8b")
    assert not _MODEL_SHAPES["anthropic"]("gpt-4o-mini")
    assert not _MODEL_SHAPES["ollama"]("gemini-3.1-flash-lite")


def test_alerts_are_disabled_in_the_shipped_config(config):
    assert config.get("alerts.enabled") is False


# Credential names that may never appear as a key in a committed config, and
# the value prefixes the common providers use. Matched against keys and
# values separately rather than against the whole blob as a substring: a
# legitimate setting like `max_output_tokens` contains "token" and is not a
# credential, and a blob match would force the config to avoid the API's own
# vocabulary.
_CREDENTIAL_KEYS = (
    "api_key", "apikey", "api-key", "password", "passwd", "secret",
    "access_token", "auth_token", "refresh_token", "bearer", "credential",
)
_CREDENTIAL_KEYS_EXACT = ("key", "token", "auth")
_CREDENTIAL_VALUE_PREFIXES = (
    "sk-", "sk_live", "pk_live", "ghp_", "github_pat_", "xoxb-", "xoxp-",
    "aws_", "akia", "-----begin",
)


# A key ending in _env names the ENVIRONMENT VARIABLE that holds the
# credential; it never holds the credential. That indirection is the whole
# point - it is how a config can say "the Reddit secret lives in
# REDDIT_CLIENT_SECRET" without containing it. Allowed, but only when the
# value really is a variable name: UPPER_SNAKE_CASE, nothing else.
_ENV_POINTER_VALUE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")


def _is_env_pointer(key: str, value) -> bool:
    return (
        str(key).lower().endswith("_env")
        and isinstance(value, str)
        and bool(_ENV_POINTER_VALUE.match(value))
    )


def _credential_findings(node, path="") -> list:
    """Every place in a config tree that looks like a stored credential."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            name = str(key).lower()
            where = f"{path}.{key}" if path else str(key)
            if _is_env_pointer(key, value):
                continue
            if any(marker in name for marker in _CREDENTIAL_KEYS) or (
                name in _CREDENTIAL_KEYS_EXACT
            ):
                found.append(f"{where}: credential-shaped key")
            found.extend(_credential_findings(value, where))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_credential_findings(value, f"{path}[{index}]"))
    elif isinstance(node, str):
        text = node.strip().lower()
        if any(text.startswith(prefix) for prefix in _CREDENTIAL_VALUE_PREFIXES):
            found.append(f"{path}: credential-shaped value")
    return found


def test_no_secret_is_stored_in_config(config):
    """Credentials belong in the environment, never in a committed file."""
    assert _credential_findings(config.raw) == []


def test_the_secret_guard_actually_catches_one():
    """A guard that cannot fail is not a guard.

    Without this, relaxing the check above would look like a passing suite.
    """
    planted = {
        "ai": {
            "provider": "anthropic",
            "api_key": "put-it-in-the-environment",
            "max_output_tokens": 2000,
        },
        "sources": [{"name": "x", "token": "abc"}],
        "deploy": {"note": "sk-live-0000000000"},
    }
    findings = _credential_findings(planted)

    assert any("ai.api_key" in f for f in findings)
    assert any("sources[0].token" in f for f in findings)
    assert any("deploy.note" in f for f in findings)
    # ...and does not fire on the legitimate settings beside them.
    assert not any("max_output_tokens" in f for f in findings)
    assert not any("provider" in f for f in findings)


def test_the_env_pointer_carve_out_cannot_hide_a_real_secret():
    """*_env may name a variable. It may not hold the value."""
    assert _credential_findings({"reddit": {"client_secret_env": "REDDIT_CLIENT_SECRET"}}) == []

    for smuggled in ("hunter2", "sk-live-abcdef", "Reddit Client Secret", "abc123def456"):
        findings = _credential_findings({"reddit": {"client_secret_env": smuggled}})
        assert findings, f"a value that is not a variable name must still be caught: {smuggled}"

    # and a key without the _env suffix gets no leniency at all
    assert _credential_findings({"reddit": {"client_secret": "REDDIT_CLIENT_SECRET"}})
