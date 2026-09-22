"""Source registry.

Adding a source means writing one module and registering it here. Nothing
downstream — normalisation, matching, clustering, scoring, reporting — needs
to know it exists.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from ..config import Config
from .base import (
    CollectionContext,
    HttpClient,
    Source,
    SourceError,
    SourceOutcome,
    extract_links,
    find_feed_links,
    parse_feed_entries,
    site_root,
)
from .bse import BseSource
from .company_ir import CompanyIrSource
from .gdelt import GdeltSource
from .google_news import GoogleNewsSource
from .government import GovernmentSource
from .nse import NseSource
from .regulators import RegulatorSource
from .rss import RssSource

__all__ = [
    "CollectionContext",
    "HttpClient",
    "Source",
    "SourceError",
    "SourceOutcome",
    "build_sources",
    "extract_links",
    "find_feed_links",
    "parse_feed_entries",
    "site_root",
    "GoogleNewsSource",
    "GdeltSource",
    "RssSource",
    "NseSource",
    "BseSource",
    "CompanyIrSource",
    "RegulatorSource",
    "GovernmentSource",
]

# Config key -> factory. Order is the order sources are polled.
SOURCE_FACTORIES: Dict[str, Callable[[Config, HttpClient, List[str]], Source]] = {
    "nse": lambda config, client, tickers: NseSource(config.section("sources").get("nse", {}), client),
    "bse": lambda config, client, tickers: BseSource(config.section("sources").get("bse", {}), client),
    "company_ir": lambda config, client, tickers: CompanyIrSource(
        config.section("sources").get("company_ir", {}), client
    ),
    "regulators": lambda config, client, tickers: RegulatorSource(
        config.section("sources").get("regulators", {}), client
    ),
    "government": lambda config, client, tickers: GovernmentSource(
        config.section("sources").get("government", {}), client
    ),
    "google_news": lambda config, client, tickers: GoogleNewsSource(
        config.section("sources").get("google_news", {}), client
    ),
    "gdelt": lambda config, client, tickers: GdeltSource(
        config.section("sources").get("gdelt", {}), client
    ),
    "rss": lambda config, client, tickers: RssSource(
        config.section("sources").get("rss", {}), client, feeds=config.feeds(tickers)
    ),
}


def build_sources(config: Config, client: HttpClient, tickers: List[str]) -> List[Source]:
    """Every enabled source, in polling order.

    Official sources are polled first so that when a story is clustered, the
    exchange filing is already present and becomes the event's primary source.
    """
    sources: List[Source] = []
    for key, factory in SOURCE_FACTORIES.items():
        if config.source_enabled(key):
            sources.append(factory(config, client, tickers))
    return sources
