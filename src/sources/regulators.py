"""Regulator feeds and listing pages (RBI, SEBI, FDA, EMA, CDSCO, APEDA...).

An original regulator announcement outranks any write-up of it, so these are
collected directly and tagged ``REGULATOR``, which both raises confidence and
earns points in the impact score.

Which regulators matter is derived from the watchlist: a run covering only
KSOLVES has no reason to poll the FDA.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..matching import fold
from ..models import Article, SourceType
from .base import (
    CollectionContext,
    HttpClient,
    Source,
    SourceError,
    extract_links,
    parse_feed_entries,
)

# regulator key -> (display name, url, kind)
REGULATOR_SOURCES: Dict[str, Tuple[str, str, str]] = {
    "rbi": ("Reserve Bank of India", "https://www.rbi.org.in/pressreleases_rss.xml", "feed"),
    "sebi": ("SEBI", "https://www.sebi.gov.in/sebirss.xml", "feed"),
    "us fda": (
        "US FDA",
        "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/press-releases/rss.xml",
        "feed",
    ),
    "ema": ("European Medicines Agency", "https://www.ema.europa.eu/en/rss.xml", "feed"),
    "cdsco": ("CDSCO", "https://cdsco.gov.in/opencms/opencms/en/Notifications/Public-Notices/", "page"),
    "apeda": ("APEDA", "https://apeda.gov.in/apedawebsite/Announcements.htm", "page"),
    "dgft": ("DGFT", "https://www.dgft.gov.in/CP/?opt=notification", "page"),
}

# Aliases seen in profiles that point at the same source.
ALIASES = {
    "reserve bank of india": "rbi",
    "usfda": "us fda",
    "fda": "us fda",
    "european medicines agency": "ema",
    "edqm": "ema",
}


class RegulatorSource(Source):
    name = "regulators"

    def _wanted(self, context: CollectionContext) -> Dict[str, List[str]]:
        """Map each regulator source onto the tickers that care about it."""
        wanted: Dict[str, List[str]] = {}
        for profile in context.profiles:
            for regulator in profile.regulators:
                key = fold(regulator)
                key = ALIASES.get(key, key)
                if key in REGULATOR_SOURCES:
                    wanted.setdefault(key, []).append(profile.ticker)
        return wanted

    def fetch(self, context: CollectionContext) -> List[Article]:
        articles: List[Article] = []
        for key, tickers in sorted(self._wanted(context).items()):
            label, url, kind = REGULATOR_SOURCES[key]
            if self.should_give_up():
                break
            self.attempted += 1
            try:
                response = self.client.get(url, headers={"Accept": "*/*"})
            except SourceError as exc:
                self.record_error(label, exc)
                continue

            if kind == "feed":
                found = list(
                    parse_feed_entries(
                        response.content,
                        feed_name=label,
                        collector=self.name,
                        source_type=SourceType.REGULATOR,
                        tickers_hint=[],
                    )
                )
            else:
                found = self._from_page(label, response.text, url)

            for article in found:
                article.is_official = True
                article.source_type = SourceType.REGULATOR
                article.raw["regulator_for"] = tickers
            if found:
                self.succeeded += 1
                self.note_success()
            else:
                self.record_note(label, "reachable but nothing parseable")
            articles.extend(found[: context.max_articles_per_query])
            self.client.sleep()
        return articles

    def _from_page(self, label: str, html: str, base_url: str) -> List[Article]:
        articles: List[Article] = []
        seen: set[str] = set()
        for url, text in extract_links(html, base_url):
            cleaned = " ".join(text.split())
            if len(cleaned) < 25 or cleaned.lower() in seen:
                continue
            seen.add(cleaned.lower())
            articles.append(
                Article(
                    title=f"{label}: {cleaned}",
                    url=url,
                    source_name=label,
                    source_type=SourceType.REGULATOR,
                    collector=self.name,
                    is_official=True,
                )
            )
        return articles
