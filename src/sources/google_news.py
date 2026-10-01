"""Google News RSS.

The workhorse. One request per generated query against the Indian locale, with
the search restricted at the source to a recent window so an old story cannot
reappear as today's news.

A company can also declare ``news_locales`` - other country editions to repeat
its *company* queries in. Google News is localised: the Spanish edition
carries the Navarrese press on Aceitunas Sarasa that the Indian edition never
shows, and the South African and Gulf editions carry equipment trade press on
HexL. Only company-level queries are repeated, and only for companies that ask
for it, because every locale is another request against a fixed budget.

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

    def locales_by_ticker(self, context: CollectionContext) -> Dict[str, List[str]]:
        return {
            profile.ticker: list(getattr(profile, "news_locales", []) or [])
            for profile in (context.profiles or [])
        }

    def build_url(
        self, query: Query, since_days: int, until_days: int = 0, locale: str = ""
    ) -> str:
        terms = query.text
        # Google's relative date operators are the only reliable way to keep a
        # daily run from rediscovering months-old stories.
        window = f" when:{max(1, int(since_days))}d"
        if until_days and until_days > 0:
            window = f" after:{_days_ago(since_days)} before:{_days_ago(until_days)}"
        search = quote_plus(terms + window)
        hl, gl, ceid = self.locale_parameters(locale)
        return f"{self.base_url}?q={search}&hl={hl}&gl={gl}&ceid={ceid}"

    def locale_parameters(self, locale: str = ""):
        """Google's own pattern: hl=<lang>, gl=<COUNTRY>, ceid=<COUNTRY>:<lang>.

        An unparseable code falls back to the configured default rather than
        building a malformed URL that would fail every query for that ticker.
        """
        if not locale:
            return self.hl, self.gl, self.ceid
        parts = str(locale).replace("_", "-").split("-")
        if len(parts) != 2 or not all(parts):
            return self.hl, self.gl, self.ceid
        lang, country = parts[0].lower(), parts[1].upper()
        return lang, country, f"{country}:{lang}"

    def _with_locales(self, queries, locales, cap: int, spent: Dict[str, int]):
        """Yield (query, locale) pairs: every query at home, some abroad."""
        for query in queries:
            yield query, ""
            if not _company_like(query):
                continue
            for locale in locales.get(query.ticker, []):
                if spent.get(query.ticker, 0) >= cap:
                    break
                spent[query.ticker] = spent.get(query.ticker, 0) + 1
                yield query, locale

    def fetch(self, context: CollectionContext) -> List[Article]:
        from ..query_generator import flatten

        queries = flatten(context.queries) if isinstance(context.queries, dict) else []
        articles: List[Article] = []
        empty_queries = 0

        locales = self.locales_by_ticker(context)
        per_ticker_cap = int(self.config.get("max_locale_queries_per_ticker", 3))
        spent: Dict[str, int] = {}

        for query, locale in self._with_locales(queries, locales, per_ticker_cap, spent):
            if self.should_give_up():
                break
            url = self.build_url(
                query, context.since_days, context.until_days, locale=locale
            )
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
                    # Deliberately no tickers_hint: a search result is not
                    # evidence of what it is about. Which query found it is
                    # recorded in raw["query_ticker"] below, for provenance.
                    tickers_hint=[],
                )
            )
            if not found:
                empty_queries += 1
            for article in found[: context.max_articles_per_query]:
                article.raw["query_kind"] = query.kind.value
                article.raw["query_ticker"] = query.ticker
                article.raw["query_term"] = query.term
                if locale:
                    article.raw["locale"] = locale
                articles.append(article)
            self.client.sleep()

        foreign = sum(spent.values())
        if foreign:
            self.record_note(
                "locale coverage",
                f"{foreign} extra request(s) in "
                + ", ".join(sorted({l for ls in locales.values() for l in ls})),
            )
        if empty_queries:
            self.record_note(
                "empty results",
                f"{empty_queries}/{self.attempted} queries returned no items in this window",
            )
        return articles


def _days_ago(days: int) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() - days * 86400))


def _company_like(query: Query) -> bool:
    """Queries worth repeating abroad: the ones naming the company itself.

    Repeating a commodity or macro query in six editions would return the
    same wire copy in six languages and spend the budget doing it.
    """
    return query.kind.value in {"COMPANY", "PRODUCT", "INTERNATIONAL"}
