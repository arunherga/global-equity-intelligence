"""AI enrichment, applied only to events that already matter.

Selection is deterministic: high-impact events, critical events, and complex
indirect events where the deterministic layer is least confident. Everything
else is left alone, which keeps a run cheap and keeps the report reproducible.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..config import Config
from ..models import Event, Relationship, StockImpact
from ..profiles.loader import CompanyProfile, Watchlist
from .base import AiProvider, AiResult, SYSTEM_PROMPT, build_prompt, extract_json, sanitise

LOG = logging.getLogger("gei.ai")


def build_provider(config: Config) -> Optional[AiProvider]:
    settings = config.section("ai")
    name = str(settings.get("provider", "ollama")).lower()
    try:
        if name == "ollama":
            from .providers.ollama import OllamaProvider

            return OllamaProvider(settings)
        if name in {"openai", "openai_compatible"}:
            from .providers.openai_provider import OpenAiProvider

            return OpenAiProvider(settings)
    except Exception as exc:  # noqa: BLE001 - AI must never break a run
        LOG.warning("could not build AI provider %r: %s", name, exc)
        return None
    LOG.warning("unknown AI provider %r", name)
    return None


def select_events(
    events: Sequence[Event], config: Config
) -> List[Tuple[Event, StockImpact]]:
    """High impact, critical, or complex-indirect events — nothing else."""
    minimum = int(config.get("ai.min_impact_score", 9))
    include_complex = bool(config.get("ai.include_indirect_complex", True))
    limit = int(config.get("ai.max_events_per_run", 12))

    chosen: List[Tuple[Event, StockImpact]] = []
    for event in events:
        for impact in event.stocks.values():
            complex_indirect = (
                include_complex
                and impact.relationship
                in {Relationship.INDIRECT_STRONG, Relationship.INDIRECT}
                and len(impact.exposures) >= 3
                and impact.impact_score >= minimum - 3
            )
            if impact.impact_score >= minimum or complex_indirect:
                chosen.append((event, impact))
    chosen.sort(key=lambda p: -p[1].impact_score)
    return chosen[:limit]


def analyse_event(
    provider: AiProvider,
    event: Event,
    impact: StockImpact,
    profile: CompanyProfile,
    config: Config,
) -> AiResult:
    exposures = "\n".join(
        f"- {e.exposure_type.value}: {e.term}" + (f" ({e.detail})" if e.detail else "")
        for e in impact.exposures[:6]
    ) or "- none recorded"
    summaries = "\n".join(
        f"- [{s.source_name or s.source_domain}] {s.title}" for s in event.sources[:5]
    ) or "- none"

    prompt = build_prompt(
        {
            "company": profile.company,
            "ticker": profile.ticker,
            "industry": profile.industry or "not recorded",
            "exchange": ", ".join(profile.exchange) or "not recorded",
            "exposures": exposures,
            "title": event.title,
            "event_date": event.event_date.isoformat() if event.event_date else "unknown",
            "categories": ", ".join(c.value for c in event.event_types) or "OTHER",
            "relationship": impact.relationship.value,
            "impact_score": impact.impact_score,
            "direction": impact.direction.value,
            "summaries": summaries,
        }
    )

    try:
        raw = provider.complete(SYSTEM_PROMPT, prompt)
    except Exception as exc:  # noqa: BLE001 - a dead model must not end the run
        return AiResult(ok=False, error=str(exc), provider=provider.name)

    parsed = extract_json(raw)
    if parsed is None:
        return AiResult(
            ok=False,
            error="response was not valid JSON",
            provider=provider.name,
        )
    cleaned, redactions = sanitise(parsed)
    return AiResult(
        ok=True,
        data=cleaned,
        provider=provider.name,
        model=str(config.get("ai.model", "")),
        redactions=redactions,
    )


def enrich(events: Sequence[Event], config: Config, watchlist: Watchlist) -> int:
    """Attach AI analysis to the events that qualify. Returns how many."""
    if not config.ai_enabled:
        return 0
    provider = build_provider(config)
    if provider is None:
        return 0

    enriched = 0
    for event, impact in select_events(events, config):
        try:
            profile = watchlist.get(impact.ticker)
        except KeyError:
            continue
        result = analyse_event(provider, event, impact, profile, config)
        if not result.ok:
            LOG.info("AI analysis failed for %s/%s: %s", event.event_id, impact.ticker, result.error)
            continue
        impact.ai_analysis = {
            "provider": result.provider,
            "model": result.model,
            **result.data,
        }
        if result.redactions:
            impact.ai_analysis["redactions"] = result.redactions
        event.record("ai analysis", f"{impact.ticker} via {result.provider}")
        enriched += 1
    return enriched
