"""AI enrichment, applied only to events that already matter.

Selection is deterministic: high-impact events, critical events, and complex
indirect events where the deterministic layer is least confident. Everything
else is left alone, which keeps a run cheap and keeps the report reproducible.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dataclasses import dataclass, field

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
        if name in {"anthropic", "claude"}:
            from .providers.anthropic_provider import AnthropicProvider

            return AnthropicProvider(settings)
        if name in {"gemini", "google"}:
            from .providers.gemini_provider import GeminiProvider

            return GeminiProvider(settings)
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
    # Everything from here to the reply is wrapped. The only guarded step
    # used to be the network call, so a malformed profile, an unexpected
    # reply shape, or an odd source title ended the whole run instead of
    # costing one analysis.
    try:
        exposures = "\n".join(
            f"- {e.exposure_type.value}: {e.term}" + (f" ({e.detail})" if e.detail else "")
            for e in impact.exposures[:6]
        ) or "- none recorded"
        summaries = "\n".join(
            f"- [{s.source_name or s.source_domain}] {s.title}"
            for s in event.sources[:5]
        ) or "- none"
        prompt = build_prompt(
            {
                "company": profile.company,
                "ticker": profile.ticker,
                "industry": profile.industry or "not recorded",
                "exchange": ", ".join(profile.exchange) or "not recorded",
                "exposures": exposures,
                "title": event.title,
                "event_date": (
                    event.event_date.isoformat() if event.event_date else "unknown"
                ),
                "categories": ", ".join(c.value for c in event.event_types) or "OTHER",
                "relationship": impact.relationship.value,
                "impact_score": impact.impact_score,
                "direction": impact.direction.value,
                "summaries": summaries,
            }
        )
    except Exception as exc:  # noqa: BLE001 - one event, not the run
        return AiResult(
            ok=False,
            error=f"could not build the prompt: {type(exc).__name__}: {exc}",
            provider=provider.name,
        )

    try:
        raw = provider.complete(SYSTEM_PROMPT, prompt)
    except Exception as exc:  # noqa: BLE001 - a dead model must not end the run
        return AiResult(ok=False, error=str(exc), provider=provider.name)

    # Parsing and sanitising sat outside the guard above, so a model reply
    # of an unexpected shape ended the entire run rather than costing one
    # analysis. Seven runs between 26 September and 4 October died in a
    # tight 7-9 minute band - which is when enrichment runs, against a
    # median successful run of 11.5 minutes.
    try:
        return _parse_and_clean(raw, provider, config)
    except Exception as exc:  # noqa: BLE001 - one bad reply, not the run
        return AiResult(
            ok=False,
            error=f"could not read the reply: {type(exc).__name__}: {exc}",
            provider=provider.name,
        )


def _parse_and_clean(raw: str, provider: AiProvider, config: Config) -> AiResult:
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


@dataclass
class EnrichmentOutcome:
    """Why the AI layer produced what it produced.

    ``selected`` and ``enriched`` have to be separate numbers. "Nothing
    qualified" and "everything I tried failed" are both zero analyses and
    completely different problems - the first is the impact threshold doing
    its job, the second is a broken key or model. Reporting only the count
    made a quiet run look like an outage.
    """

    selected: int = 0
    enriched: int = 0
    errors: List[str] = field(default_factory=list)
    # Set when the time budget stopped the layer before it worked through
    # everything it selected, so the report can say so rather than look
    # like the remaining events silently failed.
    ran_out_of_time: bool = False


def enrich(
    events: Sequence[Event],
    config: Config,
    watchlist: Watchlist,
) -> EnrichmentOutcome:
    """Attach AI analysis to the events that qualify.

    ``errors`` on the result collects the reason each analysis failed, so a
    run can say "that model name is not available" rather than only "AI
    produced nothing". A wrong model id or an unset key is otherwise
    invisible until someone reads the log.
    """
    outcome = EnrichmentOutcome()
    if not config.ai_enabled:
        return outcome
    provider = build_provider(config)
    if provider is None:
        outcome.errors.append(
            f"provider {config.get('ai.provider')!r} could not be built"
        )
        return outcome

    chosen = select_events(events, config)
    outcome.selected = len(chosen)
    # Retries multiply the worst case: twelve events, three attempts each and
    # a 90s timeout is theoretically most of an hour. The budget is what stops
    # a bad provider day from turning a ten-minute run into an open-ended one.
    budget = float(config.get("ai.max_seconds_per_run", 600))
    pause = float(config.get("ai.pause_between_calls_seconds", 1.0))
    started = time.monotonic()

    enriched = 0
    for index, (event, impact) in enumerate(chosen):
        if budget > 0 and time.monotonic() - started > budget:
            remaining = len(chosen) - index
            outcome.ran_out_of_time = True
            LOG.warning(
                "AI time budget of %.0fs spent; %d event(s) not analysed",
                budget, remaining,
            )
            outcome.errors.append(
                f"time budget of {budget:.0f}s spent, {remaining} not analysed"
            )
            break
        if index and pause > 0:
            # Twelve requests back to back is what a free tier notices.
            time.sleep(pause)
        try:
            profile = watchlist.get(impact.ticker)
        except KeyError:
            continue
        try:
            result = analyse_event(provider, event, impact, profile, config)
        except Exception as exc:  # noqa: BLE001 - belt and braces
            outcome.errors.append(f"{impact.ticker}: {type(exc).__name__}: {exc}")
            continue
        if not result.ok:
            LOG.warning(
                "AI analysis failed for %s/%s: %s",
                event.event_id, impact.ticker, result.error,
            )
            outcome.errors.append(f"{impact.ticker}: {result.error}")
            continue
        try:
            impact.ai_analysis = {
                "provider": result.provider,
                "model": result.model,
                **result.data,
            }
            if result.redactions:
                impact.ai_analysis["redactions"] = result.redactions
            event.record("ai analysis", f"{impact.ticker} via {result.provider}")
        except Exception as exc:  # noqa: BLE001 - one event, not the run
            impact.ai_analysis = None
            outcome.errors.append(
                f"{impact.ticker}: could not attach the analysis: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        enriched += 1
    outcome.enriched = enriched
    return outcome
