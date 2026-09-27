"""The optional AI layer, and the guardrails around it."""

from __future__ import annotations

import json
from datetime import date

import pytest

from src.ai import build_prompt, extract_json, sanitise, select_events
from src.ai.analyzer import analyse_event, build_provider
from src.config import load_config
from src.models import Direction, Event, EventCategory, Relationship, StockImpact


def make_event(impact_score=10, relationship=Relationship.DIRECT, exposures=()):
    event = Event(
        event_id="EVENT-WAAREEENER-2026-0001",
        title="Waaree bags a 1.2 GW export order",
        event_date=date(2026, 9, 22),
        event_types=[EventCategory.EXPORT_ORDER],
    )
    event.stocks["WAAREEENER"] = StockImpact(
        ticker="WAAREEENER", relationship=relationship, impact_score=impact_score,
        direction=Direction.POSITIVE, confidence=0.8, exposures=list(exposures),
    )
    return event


def test_a_config_that_does_not_mention_ai_is_off(tmp_path):
    """The invariant that matters, now that the shipped config turns it on.

    Enabling the layer must always be a deliberate line in a config file or
    an explicit environment variable - never something a missing section
    falls into. Built from an empty file rather than from overrides, because
    overrides deep-merge onto the shipped config and would inherit its
    `enabled: true`.
    """
    from src.config import Config

    assert Config(raw={}, root=tmp_path).ai_enabled is False
    assert Config(raw={"ai": {}}, root=tmp_path).ai_enabled is False
    assert Config(raw={"ai": {"provider": "gemini"}}, root=tmp_path).ai_enabled is False


def test_no_ai_beats_the_environment(monkeypatch):
    """--no-ai is a brake; an exported variable must not release it."""
    monkeypatch.setenv("GEI_AI_ENABLED", "true")

    assert load_config().ai_enabled is True  # the variable does work
    forced_off = load_config(overrides={"ai": {"enabled": False, "force_off": True}})
    assert forced_off.ai_enabled is False


def test_an_offline_run_never_calls_a_model(monkeypatch):
    """`--offline` is a promise of no network; a hosted provider breaks it.

    This is also what keeps the sample report reproducible: it is generated
    offline, so it cannot vary with whether an API key is in the environment.
    """
    from src.profiles import load_watchlist
    from src.main import Pipeline

    called = {"n": 0}

    def explode(*args, **kwargs):
        called["n"] += 1
        raise AssertionError("an offline run must not reach a provider")

    monkeypatch.setattr("src.ai.enrich", explode)

    config = load_config()
    assert config.ai_enabled is True, "the shipped config enables AI"
    watchlist = load_watchlist(config=config)

    result = Pipeline(config, watchlist, watchlist.profiles).run(
        run_date=date(2026, 9, 22),
        since_days=2,
        dry_run=True,
        offline=True,
        resolve_links=False,
        articles_override=_offline_fixture_articles(),
    )

    assert called["n"] == 0
    assert not any(d.source == "ai_enrichment" for d in result.diagnostics)
    assert all(
        impact.ai_analysis is None
        for event in result.events
        for impact in event.stocks.values()
    )


def _offline_fixture_articles():
    from datetime import datetime, timezone
    from scripts.generate_sample_report import load_fixture_articles

    return load_fixture_articles(now=datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc))


def test_only_important_events_are_escalated(config):
    events = [make_event(impact_score=3), make_event(impact_score=12)]
    chosen = select_events(events, config)
    assert len(chosen) == 1
    assert chosen[0][1].impact_score == 12


def test_complex_indirect_events_are_escalated(config):
    from src.models import ExposureMatch, ExposureType

    exposures = [
        ExposureMatch(exposure_type=ExposureType.COMMODITY, term="polysilicon",
                      relationship=Relationship.INDIRECT),
        ExposureMatch(exposure_type=ExposureType.COMPETITOR, term="LONGi",
                      relationship=Relationship.INDIRECT),
        ExposureMatch(exposure_type=ExposureType.GEOGRAPHY, term="China",
                      relationship=Relationship.WEAK),
    ]
    event = make_event(impact_score=7, relationship=Relationship.INDIRECT_STRONG,
                       exposures=exposures)
    assert select_events([event], config)


def test_prompt_contains_the_company_and_the_schema():
    prompt = build_prompt({
        "company": "Waaree Energies Limited", "ticker": "WAAREEENER",
        "industry": "Solar", "exchange": "NSE", "exposures": "- none",
        "title": "Order win", "event_date": "2026-09-22", "categories": "EXPORT_ORDER",
        "relationship": "DIRECT", "impact_score": 11, "direction": "POSITIVE",
        "summaries": "- none",
    })
    assert "Waaree Energies Limited" in prompt
    assert "revenue_effect" in prompt
    assert "monitor_next" in prompt
    assert "Do not recommend" in prompt


def test_json_is_extracted_from_a_chatty_response():
    parsed = extract_json('Sure!\n```json\n{"revenue_effect": "higher"}\n```')
    assert parsed == {"revenue_effect": "higher"}
    assert extract_json("no json here") is None


def test_recommendations_are_stripped():
    cleaned, redactions = sanitise({
        "revenue_effect": "Investors should BUY this stock now",
        "monitor_next": ["order book", "target price of 500"],
    })
    assert "buy" not in cleaned["revenue_effect"].lower()
    assert cleaned["monitor_next"] == ["order book"]
    assert redactions


def test_the_schema_is_always_complete():
    cleaned, _ = sanitise({"revenue_effect": "higher"})
    for key in ("margin_effect", "cost_effect", "key_uncertainty", "monitor_next"):
        assert key in cleaned


def test_a_dead_model_does_not_break_the_run(config, watchlist):
    class DeadProvider:
        name = "dead"

        def complete(self, system, prompt):
            raise RuntimeError("connection refused")

    event = make_event()
    result = analyse_event(DeadProvider(), event, event.stocks["WAAREEENER"],
                           watchlist.get("WAAREEENER"), config)
    assert not result.ok
    assert "connection refused" in result.error


def test_a_garbage_response_is_rejected(config, watchlist):
    class BabblingProvider:
        name = "babbler"

        def complete(self, system, prompt):
            return "I think this is probably fine, no JSON though"

    event = make_event()
    result = analyse_event(BabblingProvider(), event, event.stocks["WAAREEENER"],
                           watchlist.get("WAAREEENER"), config)
    assert not result.ok


def test_a_good_response_is_attached(config, watchlist):
    class GoodProvider:
        name = "good"

        def complete(self, system, prompt):
            return (
                '{"revenue_effect": "material addition to FY27 revenue", '
                '"margin_effect": "unclear", "monitor_next": ["execution schedule"]}'
            )

    event = make_event()
    result = analyse_event(GoodProvider(), event, event.stocks["WAAREEENER"],
                           watchlist.get("WAAREEENER"), config)
    assert result.ok
    assert "FY27" in result.data["revenue_effect"]
    assert result.data["monitor_next"] == ["execution schedule"]


def test_unknown_provider_returns_none():
    config = load_config(overrides={"ai": {"provider": "definitely-not-a-provider"}})
    assert build_provider(config) is None


# -- the Anthropic provider ----------------------------------------------
#
# No live call is made anywhere in this file. The transport is stubbed, so
# what is checked is the request this code builds and how it reads a reply -
# not that a key works, which only `--check-ai` can establish.


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _capture(monkeypatch, response):
    """Swap requests.post for a recorder and hand back the call it saw."""
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers or {}
        seen["body"] = json or {}
        seen["timeout"] = timeout
        return response

    monkeypatch.setattr("src.ai.providers.anthropic_provider.requests.post", fake_post)
    return seen


def _provider(**overrides):
    from src.ai.providers.anthropic_provider import AnthropicProvider

    settings = {"model": "claude-sonnet-4-5", "timeout_seconds": 30, "temperature": 0.1}
    settings.update(overrides)
    return AnthropicProvider(settings)


def test_anthropic_builds_a_messages_api_request(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    response = _FakeResponse(
        payload={"content": [{"type": "text", "text": '{"revenue_effect": "ok"}'}]}
    )
    seen = _capture(monkeypatch, response)

    out = _provider().complete("SYSTEM", "PROMPT")

    assert out == '{"revenue_effect": "ok"}'
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["headers"]["x-api-key"] == "test-key-not-real"
    assert "Authorization" not in seen["headers"]
    body = seen["body"]
    # The system prompt is a top-level field, not a message - the Messages
    # API rejects role: system.
    assert body["system"] == "SYSTEM"
    assert body["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert body["max_tokens"] >= 2000
    assert body["model"] == "claude-sonnet-4-5"
    assert seen["timeout"] == 30


def test_anthropic_joins_multiple_text_blocks_and_skips_the_rest(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    response = _FakeResponse(
        payload={
            "content": [
                {"type": "thinking", "thinking": "should not appear"},
                {"type": "text", "text": '{"revenue_effect":'},
                {"type": "text", "text": ' "ok"}'},
            ]
        }
    )
    _capture(monkeypatch, response)

    out = _provider().complete("SYSTEM", "PROMPT")

    assert out == '{"revenue_effect": "ok"}'
    assert extract_json(out) == {"revenue_effect": "ok"}


def test_anthropic_without_a_key_refuses_before_making_a_request(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    def explode(*args, **kwargs):
        raise AssertionError("no request may be made without a key")

    monkeypatch.setattr("src.ai.providers.anthropic_provider.requests.post", explode)

    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY is not set"):
        _provider().complete("SYSTEM", "PROMPT")


def test_anthropic_surfaces_the_api_error_message(monkeypatch):
    """A wrong model name is the likeliest misconfiguration; say so."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    response = _FakeResponse(
        status_code=404,
        payload={"error": {"type": "not_found_error", "message": "model: nope-1"}},
    )
    _capture(monkeypatch, response)

    with pytest.raises(RuntimeError) as excinfo:
        _provider(model="nope-1").complete("SYSTEM", "PROMPT")

    message = str(excinfo.value)
    assert "404" in message
    assert "model: nope-1" in message
    assert "test-key-not-real" not in message  # the key never reaches an error


def test_anthropic_survives_a_non_json_error_page(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    response = _FakeResponse(status_code=502, payload=None, text="<html>bad gateway")
    _capture(monkeypatch, response)

    with pytest.raises(RuntimeError, match="502"):
        _provider().complete("SYSTEM", "PROMPT")


def test_build_provider_accepts_anthropic_and_claude():
    from src.ai.providers.anthropic_provider import AnthropicProvider

    for name in ("anthropic", "claude", "Anthropic"):
        config = load_config(overrides={"ai": {"provider": name}})
        provider = build_provider(config)
        assert isinstance(provider, AnthropicProvider), name
        assert provider.name == "anthropic"


def test_advice_from_the_model_is_still_redacted(monkeypatch):
    """The guardrail lives above the provider, so it covers this one too."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    response = _FakeResponse(
        payload={
            "content": [
                {
                    "type": "text",
                    "text": '{"revenue_effect": "Strong order book; we would buy '
                            'this stock at a target price of 4000.", '
                            '"monitor_next": ["Accumulate on dips", "Q3 order inflow"]}',
                }
            ]
        }
    )
    _capture(monkeypatch, response)

    config = load_config(overrides={"ai": {"provider": "anthropic"}})
    result = analyse_event(
        build_provider(config),
        make_event(),
        make_event().stocks["WAAREEENER"],
        next(p for p in __import__("src.profiles", fromlist=["load_watchlist"])
             .load_watchlist(config=config) if p.ticker == "WAAREEENER"),
        config,
    )

    assert result.ok
    assert "buy" not in result.data["revenue_effect"].lower()
    assert "target price" not in result.data["revenue_effect"].lower()
    assert "[redacted]" in result.data["revenue_effect"]
    assert result.data["monitor_next"] == ["Q3 order inflow"]
    assert result.redactions


def test_enrich_reports_why_it_produced_nothing(monkeypatch):
    """"AI produced nothing" and "that model is unavailable" must differ."""
    from src.ai import enrich
    from src.profiles import load_watchlist

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    response = _FakeResponse(
        status_code=404,
        payload={"error": {"message": "model: claude-does-not-exist"}},
    )
    _capture(monkeypatch, response)

    config = load_config(
        overrides={
            "ai": {
                "enabled": True,
                "provider": "anthropic",
                "model": "claude-does-not-exist",
            }
        }
    )
    watchlist = load_watchlist(config=config)

    outcome = enrich([make_event(impact_score=12)], config, watchlist)

    assert outcome.selected == 1, "the event qualified, so a call was attempted"
    assert outcome.enriched == 0
    assert outcome.errors == [
        "WAAREEENER: 404 from Anthropic: model: claude-does-not-exist"
    ]


def test_nothing_qualifying_is_not_a_failure(monkeypatch):
    """The distinction this whole outcome object exists for.

    A quiet run - every event below the threshold - used to be reported as
    ai_enrichment FAILED, which reads as a broken integration and sent me
    hunting for one. No call is attempted, so there is nothing to fail.
    """
    from src.ai import enrich
    from src.profiles import load_watchlist

    def explode(*args, **kwargs):
        raise AssertionError("no provider call may be made")

    monkeypatch.setattr("src.ai.providers.openai_provider.requests.post", explode)

    config = load_config(overrides={"ai": {"enabled": True, "provider": "gemini",
                                           "model": "gemini-3.1-flash-lite"}})
    watchlist = load_watchlist(config=config)

    outcome = enrich([make_event(impact_score=4)], config, watchlist)

    assert outcome.selected == 0
    assert outcome.enriched == 0
    assert outcome.errors == []


def test_an_unbuildable_provider_says_so(monkeypatch):
    from src.ai import enrich
    from src.profiles import load_watchlist

    config = load_config(overrides={"ai": {"enabled": True, "provider": "nonsense"}})
    watchlist = load_watchlist(config=config)

    outcome = enrich([make_event(impact_score=12)], config, watchlist)

    assert outcome.enriched == 0
    assert outcome.errors == ["provider 'nonsense' could not be built"]


# -- the Gemini provider --------------------------------------------------
#
# Gemini is the OpenAI dialect with two edges that matter: no documented
# response_format, and its own key variable. Both are asserted here, because
# sending an unsupported parameter is how a working call becomes a 400.


def _capture_openai(monkeypatch, response):
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen["url"] = url
        seen["headers"] = headers or {}
        seen["body"] = json or {}
        return response

    monkeypatch.setattr("src.ai.providers.openai_provider.requests.post", fake_post)
    return seen


def _chat_response(text='{"revenue_effect": "ok"}', status_code=200, payload=None):
    if payload is None:
        payload = {"choices": [{"message": {"role": "assistant", "content": text}}]}
    return _FakeResponse(status_code=status_code, payload=payload)


def _gemini(**overrides):
    from src.ai.providers.gemini_provider import GeminiProvider

    settings = {"timeout_seconds": 30, "temperature": 0.1}
    settings.update(overrides)
    return GeminiProvider(settings)


def test_gemini_omits_response_format(monkeypatch):
    """Google's compat layer does not document it; an unknown field 400s."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    seen = _capture_openai(monkeypatch, _chat_response())

    out = _gemini().complete("SYSTEM", "PROMPT")

    assert out == '{"revenue_effect": "ok"}'
    assert "response_format" not in seen["body"]
    assert seen["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    )
    assert seen["headers"]["Authorization"] == "Bearer test-key-not-real"
    assert seen["body"]["messages"][0]["role"] == "system"


def test_openai_still_asks_for_json_mode(monkeypatch):
    """The refactor must not quietly drop it where it is supported."""
    from src.ai.providers.openai_provider import OpenAiProvider

    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    seen = _capture_openai(monkeypatch, _chat_response())

    OpenAiProvider({"model": "gpt-4o-mini"}).complete("SYSTEM", "PROMPT")

    assert seen["body"]["response_format"] == {"type": "json_object"}
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"


def test_gemini_accepts_google_api_key_as_a_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-side-key")
    _capture_openai(monkeypatch, _chat_response())

    assert _gemini().complete("SYSTEM", "PROMPT")


def test_gemini_without_a_key_names_the_right_variable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="GEMINI_API_KEY is not set"):
        _gemini().complete("SYSTEM", "PROMPT")


def test_a_hosted_provider_ignores_a_local_base_url(monkeypatch):
    """config.yaml ships base_url pointing at Ollama.

    If a hosted provider honoured it, switching provider alone would send
    every call to localhost and look like a dead model.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    monkeypatch.delenv("GEMINI_BASE_URL", raising=False)

    provider = _gemini(base_url="http://localhost:11434")

    assert "localhost" not in provider.base_url
    assert provider.base_url.startswith("https://generativelanguage.googleapis.com")


def test_gemini_rate_limit_is_reported_as_itself(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    _capture_openai(
        monkeypatch,
        _chat_response(
            status_code=429,
            payload={"error": {"message": "Quota exceeded for requests per day"}},
        ),
    )

    with pytest.raises(RuntimeError) as excinfo:
        _gemini().complete("SYSTEM", "PROMPT")

    message = str(excinfo.value)
    assert "429" in message
    assert "Quota exceeded" in message
    assert "test-key-not-real" not in message


def test_gemini_survives_a_reply_with_no_choices(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    _capture_openai(monkeypatch, _chat_response(payload={"choices": []}))

    assert _gemini().complete("SYSTEM", "PROMPT") == ""


def test_gemini_json_in_a_code_fence_still_parses(monkeypatch):
    """Without json mode a model may fence its answer; extract_json copes."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    fenced = '```json\n{"revenue_effect": "ok"}\n```'
    _capture_openai(monkeypatch, _chat_response(text=fenced))

    raw = _gemini().complete("SYSTEM", "PROMPT")

    assert extract_json(raw) == {"revenue_effect": "ok"}


# -- --check-ai, the two-step verification --------------------------------


def _stub_event(ticker="WAAREEENER", score=11):
    event = make_event(impact_score=score)
    if ticker != "WAAREEENER":
        impact = event.stocks.pop("WAAREEENER")
        impact.ticker = ticker
        event.stocks[ticker] = impact
    return event


def test_best_stored_event_ranks_on_impact_and_skips_unknown_tickers(monkeypatch):
    """Picks the event a run would care most about, not the first one."""
    import src.main as main_mod
    from src.profiles import load_watchlist

    config = load_config()
    watchlist = load_watchlist(config=config)

    small = _stub_event(score=6)
    big = _stub_event(score=14)
    delisted = _stub_event(ticker="NOTINWATCHLIST", score=15)

    class _Entry:
        def __init__(self, eid, impact):
            self.event_id, self.max_impact = eid, impact

    events = {"small": small, "big": big, "gone": delisted}

    class _Store:
        index = {
            "gone": _Entry("gone", 15),
            "small": _Entry("small", 6),
            "big": _Entry("big", 14),
        }

        def load(self):
            return self

        def get(self, eid):
            return events[eid]

    monkeypatch.setattr(main_mod, "EventStore", lambda *a, **k: _Store())

    event, impact, profile = main_mod._best_stored_event(config, watchlist)

    # 15 is highest but its ticker has no profile, so it is skipped.
    assert impact.impact_score == 14
    assert impact.ticker == "WAAREEENER"
    assert profile.ticker == "WAAREEENER"


def test_check_ai_runs_the_real_prompt_and_shows_the_guardrail(monkeypatch, capsys):
    """A one-field probe cannot stand in for the real ten-field prompt.

    Without json mode - Gemini's case - the risk is the model answering
    unparseably on the long prompt while sailing through a trivial one.
    """
    import src.main as main_mod
    from src.profiles import load_watchlist

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")

    answer = {key: f"text for {key}" for key, _ in main_mod._AI_FIELDS}
    answer["revenue_effect"] = "Strong book; we would buy at a target price of 500."
    answer["monitor_next"] = ["the exchange filing"]
    _capture_openai(monkeypatch, _chat_response(text=json.dumps(answer)))

    config = load_config()
    watchlist = load_watchlist(config=config)
    event = _stub_event(score=12)
    monkeypatch.setattr(
        main_mod, "_best_stored_event",
        lambda c, w: (event, event.stocks["WAAREEENER"], w.get("WAAREEENER")),
    )

    assert main_mod._check_ai(config, watchlist) == 0

    out = capsys.readouterr().out
    assert "Now the real analysis prompt" in out
    assert f"{len(main_mod._AI_FIELDS)} of {len(main_mod._AI_FIELDS)} fields answered" in out
    assert "guardrail fired" in out
    assert "[redacted]" in out
    assert "buy" not in out.split("-- AI analysis")[1]
    assert "added to a run" in out


def test_check_ai_fails_loudly_when_the_long_prompt_is_not_json(monkeypatch, capsys):
    import src.main as main_mod
    from src.profiles import load_watchlist

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    config = load_config()
    watchlist = load_watchlist(config=config)
    event = _stub_event(score=12)
    monkeypatch.setattr(
        main_mod, "_best_stored_event",
        lambda c, w: (event, event.stocks["WAAREEENER"], w.get("WAAREEENER")),
    )

    replies = iter([
        _chat_response(text='{"revenue_effect": "ok"}'),   # the probe passes
        _chat_response(text="Sure! Here are my thoughts in prose."),  # the real one
    ])
    monkeypatch.setattr(
        "src.ai.providers.openai_provider.requests.post",
        lambda *a, **k: next(replies),
    )

    assert main_mod._check_ai(config, watchlist) == 1

    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "not valid JSON" in out
    assert "stronger model" in out


def test_check_ai_says_so_when_there_is_nothing_stored_to_analyse(monkeypatch, capsys):
    import src.main as main_mod
    from src.profiles import load_watchlist

    monkeypatch.setenv("GEMINI_API_KEY", "test-key-not-real")
    _capture_openai(monkeypatch, _chat_response())
    monkeypatch.setattr(main_mod, "_best_stored_event", lambda c, w: None)

    config = load_config()
    assert main_mod._check_ai(config, load_watchlist(config=config)) == 0

    out = capsys.readouterr().out
    assert "SKIPPED" in out
    assert "run the pipeline once" in out
