"""Company investor-relations pages.

The original source is always preferred: a company's own press release beats
the third re-write of it. IR sites are inconsistent, so this tries the feed the
page advertises first and falls back to extracting dated-looking links from the
investor page. Sites that block automated requests are recorded, not retried
forever.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..matching import contains_any, fold
from ..models import Article, SourceType
from .base import (
    CollectionContext,
    HttpClient,
    Source,
    SourceError,
    extract_links,
    find_feed_links,
    parse_feed_entries,
)

# Anchor text that suggests an announcement rather than site furniture.
ANNOUNCEMENT_HINTS = (
    "press release", "announcement", "intimation", "disclosure", "results",
    "investor", "update", "outcome", "order", "expansion", "media", "news",
    "regulation 30", "board meeting", "earnings", "presentation",
)

SKIP_HINTS = (
    "privacy", "cookie", "terms", "careers", "contact us", "sitemap",
    "login", "subscribe", "unsubscribe", "home",
)


class CompanyIrSource(Source):
    name = "company_ir"

    def __init__(self, config: Dict[str, Any], client: HttpClient) -> None:
        super().__init__(config, client)
        self.max_links = int(config.get("max_links_per_company", 12))

    def fetch(self, context: CollectionContext) -> List[Article]:
        articles: List[Article] = []
        for profile in context.profiles:
            page_url = profile.ir.get("investor_page") or profile.ir.get("website")
            if not page_url:
                self.record_note(profile.ticker, "no IR page configured")
                continue

            if self.should_give_up():
                break
            self.attempted += 1
            try:
                response = self.client.get(page_url, headers={"Accept": "text/html,*/*"})
            except SourceError as exc:
                self.record_error(f"{profile.ticker} IR page", exc)
                continue

            html = response.text
            found = self._from_feed(profile, html, page_url)
            if not found:
                found = self._from_links(profile, html, page_url)

            if found:
                self.succeeded += 1
                self.note_success()
            else:
                self.record_note(profile.ticker, "IR page reachable but nothing parseable")
            articles.extend(found[: self.max_links])
            self.client.sleep()
        return articles

    def _from_feed(self, profile, html: str, page_url: str) -> List[Article]:
        for feed_url in find_feed_links(html, page_url)[:1]:
            try:
                content = self.client.get(feed_url).content
            except SourceError:
                continue
            items = list(
                parse_feed_entries(
                    content,
                    feed_name=f"{profile.company} IR",
                    collector=self.name,
                    source_type=SourceType.COMPANY_IR,
                    tickers_hint=[profile.ticker],
                )
            )
            for item in items:
                item.is_official = True
            if items:
                self.record_note(profile.ticker, f"using IR feed {feed_url}")
                return items
        return []

    def _from_links(self, profile, html: str, page_url: str) -> List[Article]:
        articles: List[Article] = []
        seen: set[str] = set()
        for url, text in extract_links(html, page_url):
            folded = fold(text)
            if len(folded) < 12 or folded in seen:
                continue
            if contains_any(folded, SKIP_HINTS):
                continue
            if not contains_any(folded, ANNOUNCEMENT_HINTS):
                continue
            seen.add(folded)
            articles.append(
                Article(
                    title=f"{profile.company}: {text.strip()}",
                    url=url,
                    source_name=f"{profile.company} investor relations",
                    source_type=SourceType.COMPANY_IR,
                    collector=self.name,
                    tickers_hint=[profile.ticker],
                    is_official=True,
                )
            )
        return articles
