"""Google News RSS.

The workhorse. One request per generated query against the Indian locale, with
the search restricted at the source to a recent window so an old story cannot
reappear as today's news.

Google hands back a redirect link rather than the publisher's URL; resolving
those is deliberately a separate, late step (:mod:`src.resolve`) so a run does
not spend hundreds of requests on links that will never reach the report.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from ..models import Article, SourceType
from ..query_generator import Query
from .base import CollectionContext, HttpClient, Source, SourceError, parse_feed_entries


class GoogleNewsSource(Source):
    name = "google_news"

    def __init__(self, config: Dict[str, Any], client: HttpClient) -> None:
        super().__init__(config, client)
        self.base_url = str(config.get("base_url", "https://news.google.com/rss/search"))
        self.hl = str(config.get("hl", "en-IN"))
        self.gl = str(config.get("gl", "IN"))
        self.ceid = str(config.get("ceid", "IN:en"))

    def build_url(self, query: Query, since_days: int, until_days: int = 0) -> str:
        terms = query.text
        # Google's relative date operators are the only reliable way to keep a
        # daily run from rediscovering months-old stories.
        window = f" when:{max(1, int(since_days))}d"
        if until_days and until_days > 0:
            window = f" after:{_days_ago(since_days)} before:{_days_ago(until_days)}"
        search = quote_plus(terms + window)
        return f"{self.base_url}?q={search}&hl={self.hl}&gl={self.gl}&ceid={self.ceid}"

    def fetch(self, context: CollectionContext) -> List[Article]:
        from ..query_generator import flatten

        queries = flatten(context.queries) if isinstance(context.queries, dict) else []
        articles: List[Article] = []
        empty_queries = 0

        for query in queries:
            if self.should_give_up():
                break
            url = self.build_url(query, context.since_days, context.until_days)
            self.attempted += 1
            if context.dry_run and self.config.get("skip_network_on_dry_run"):
                continue
            try:
                response = self.client.get(url)
            except SourceError as exc:
                self.record_error(f"query {query.text!r}", exc)
                continue
            self.succeeded += 1
            self.note_success()
            found = list(
                parse_feed_entries(
                    response.content,
                    feed_name="Google News",
                    collector=self.name,
                    source_type=SourceType.UNKNOWN_NEWS_SITE,
                    query=query.text,
                    tickers_hint=[query.ticker] if query.kind.value == "COMPANY" else [],
                )
            )
            if not found:
                empty_queries += 1
            for article in found[: context.max_articles_per_query]:
                article.raw["query_kind"] = query.kind.value
                article.raw["query_ticker"] = query.ticker
                article.raw["query_term"] = query.term
                articles.append(article)
            self.client.sleep()

        if empty_queries:
            self.record_note(
                "empty results",
                f"{empty_queries}/{self.attempted} queries returned no items in this window",
            )
        return articles


def _days_ago(days: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))
