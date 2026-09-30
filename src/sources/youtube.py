"""YouTube, where Indian personal-care buying decisions actually happen.

A written review is a form filled in. A fifteen-minute video with a comment
count is someone spending real time, and for haircare and skincare it is
where the audience is. Equipment buyers do the same for machinery.

Two practical advantages over the Reddit source. It is key-authenticated,
so it works from a GitHub Actions runner - Reddit's anonymous endpoint
returns 403 Blocked from datacentre addresses, which is what killed the
consumer signal on 2026-09-29. And the quota is explicit rather than
guessed at.

That quota is the constraint worth designing around. Google allocates a
project "100 search.list calls" a day. Not ten thousand units to spend
freely: a hundred searches. So the per-run cap is deliberately low, it is
enforced before any request is made, and a config change cannot silently
blow a day's allowance - a test pins that.

What comes back is opinion. Items are marked SOCIAL_MEDIA and carry
``raw["consumer"] = True``, so they reach the sentiment layer and never the
event pipeline.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from ..models import Article, SourceType
from .base import CollectionContext, HttpClient, Source, SourceError

API_URL = "https://www.googleapis.com/youtube/v3/search"

# Google's documented default allocation is 100 search.list calls per day.
# A run that tried to use more than a fraction of that would leave nothing
# for the rest of the day, so this is the ceiling whatever config asks for.
HARD_QUERY_CEILING = 20


class YouTubeSource(Source):
    name = "youtube"

    def __init__(self, config: Dict[str, Any], client: HttpClient) -> None:
        super().__init__(config, client)
        self.api_url = str(config.get("api_url", API_URL))
        self.api_key = os.environ.get(
            str(config.get("api_key_env", "YOUTUBE_API_KEY")), ""
        ).strip()
        self.key_variable = str(config.get("api_key_env", "YOUTUBE_API_KEY"))
        self.max_results = max(1, min(int(config.get("max_results_per_query", 10)), 50))
        self.max_queries = min(
            int(config.get("max_queries_per_run", 8)), HARD_QUERY_CEILING
        )
        self.window_days = int(config.get("window_days", 14))
        self.region = str(config.get("region_code", "IN"))
        self.language = str(config.get("relevance_language", "en"))

    # -- url building ----------------------------------------------------
    def build_url(self, term: str, now: Optional[datetime] = None) -> str:
        published_after = (
            (now or datetime.now(timezone.utc)) - timedelta(days=self.window_days)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        return (
            f"{self.api_url}?part=snippet&type=video&order=date"
            f"&q={quote_plus(term)}"
            f"&maxResults={self.max_results}"
            f"&publishedAfter={published_after}"
            f"&regionCode={self.region}"
            f"&relevanceLanguage={self.language}"
            f"&key={quote_plus(self.api_key)}"
        )

    def terms_for(self, context: CollectionContext):
        for profile in context.profiles or []:
            for term in getattr(profile, "consumer_terms", []) or []:
                yield profile.ticker, str(term)

    # -- collection ------------------------------------------------------
    def fetch(self, context: CollectionContext) -> List[Article]:
        if not self.api_key:
            self.record_skipped(
                f"{self.key_variable} is not set; no quota spent"
            )
            return []

        articles: List[Article] = []
        spent = 0
        for ticker, term in self.terms_for(context):
            if spent >= self.max_queries or self.should_give_up():
                break
            url = self.build_url(term)
            self.attempted += 1
            spent += 1
            if context.dry_run and self.config.get("skip_network_on_dry_run"):
                continue
            try:
                response = self.client.get(url)
            except SourceError as exc:
                # A quota failure is not a broken source and should read as
                # itself; everything else is a genuine error.
                if "403" in str(exc) or "quota" in str(exc).lower():
                    self.record_error(f"{term!r}: quota or access refused", exc)
                else:
                    self.record_error(f"{term!r}", exc)
                continue
            self.succeeded += 1
            self.note_success()
            try:
                payload = response.json()
            except ValueError as exc:
                self.record_error(f"{term!r}: response was not JSON", exc)
                continue
            articles.extend(self.parse(payload, ticker=ticker, term=term))
            self.client.sleep()

        if spent:
            self.record_note(
                "quota",
                f"{spent} search call(s) of a documented 100/day allocation",
            )
        return articles

    # -- parsing ---------------------------------------------------------
    def parse(self, payload: Dict[str, Any], ticker: str, term: str) -> List[Article]:
        items = (payload or {}).get("items") or []
        articles: List[Article] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            video_id = ((item.get("id") or {}) if isinstance(item.get("id"), dict) else {}).get(
                "videoId"
            )
            snippet = item.get("snippet") or {}
            title = str(snippet.get("title") or "").strip()
            if not video_id or not title:
                continue
            article = Article(
                title=_unescape(title),
                url=f"https://www.youtube.com/watch?v={video_id}",
                source_name=str(snippet.get("channelTitle") or "YouTube"),
                source_domain="youtube.com",
                source_type=SourceType.SOCIAL_MEDIA,
                summary=_unescape(str(snippet.get("description") or ""))[:600],
                published=_parse_iso(snippet.get("publishedAt")),
                collector=self.name,
                query=term,
                tickers_hint=[ticker],
            )
            article.raw.update({
                "consumer": True,
                "channel": str(snippet.get("channelTitle") or ""),
                "video_id": video_id,
                "query_ticker": ticker,
                "query_term": term,
            })
            articles.append(article)
        return articles


def _unescape(text: str) -> str:
    """YouTube returns HTML entities in titles and descriptions."""
    import html

    return html.unescape(text or "").strip()


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
