"""GDELT Document API.

Used mainly for the *international* exposure topics — foreign coverage that an
India-locale news search under-reports. GDELT is free and needs no key, but it
rate-limits and occasionally answers with HTML instead of JSON, so every
response is validated before use and a failure is recorded, not raised.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List
from urllib.parse import quote_plus

from ..models import Article, SourceType
from ..normalize import parse_date
from .base import CollectionContext, HttpClient, Source, SourceError


class GdeltSource(Source):
    name = "gdelt"

    def __init__(self, config: Dict[str, Any], client: HttpClient) -> None:
        super().__init__(config, client)
        self.base_url = str(
            config.get("base_url", "https://api.gdeltproject.org/api/v2/doc/doc")
        )
        self.max_records = int(config.get("max_records", 40))
        self.international_only = bool(config.get("international_only", True))

    def build_url(self, query: str, since_days: int) -> str:
        timespan = f"{max(1, int(since_days)) * 24}h"
        return (
            f"{self.base_url}?query={quote_plus(query)}"
            f"&mode=ArtList&format=json&maxrecords={self.max_records}"
            f"&timespan={timespan}&sort=DateDesc"
        )

    def fetch(self, context: CollectionContext) -> List[Article]:
        from ..query_generator import flatten

        queries = flatten(context.queries) if isinstance(context.queries, dict) else []
        if self.international_only:
            queries = [q for q in queries if q.international]
        articles: List[Article] = []

        for query in queries:
            if self.should_give_up():
                break
            # GDELT's parser dislikes quoted phrases combined with operators.
            text = query.text.replace('"', "").strip()
            if len(text.split()) > 8:
                text = " ".join(text.split()[:8])
            self.attempted += 1
            try:
                response = self.client.get(self.build_url(text, context.since_days))
                payload = response.json()
            except SourceError as exc:
                self.record_error(f"query {text!r}", exc)
                continue
            except (json.JSONDecodeError, ValueError) as exc:
                # GDELT answers overload with an HTML error page.
                self.record_error(f"query {text!r}", f"non-JSON response ({exc})")
                continue

            items = payload.get("articles") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                self.record_note(f"query {text!r}", "no articles key in response")
                continue
            self.succeeded += 1
            self.note_success()

            for item in items[: context.max_articles_per_query]:
                url = item.get("url") or ""
                title = (item.get("title") or "").strip()
                if not url or not title:
                    continue
                article = Article(
                    title=title,
                    url=url,
                    source_name=item.get("domain", ""),
                    source_domain=item.get("domain", ""),
                    source_type=SourceType.UNKNOWN_NEWS_SITE,
                    published=parse_date(item.get("seendate")),
                    collector=self.name,
                    query=text,
                    language=item.get("language", "en") or "en",
                )
                article.raw["query_kind"] = query.kind.value
                article.raw["query_ticker"] = query.ticker
                article.raw["gdelt_country"] = item.get("sourcecountry", "")
                articles.append(article)
            self.client.sleep()

        return articles
