"""Configured RSS feeds: general business press and sector trade publications.

Feeds live in ``config.yaml`` under ``feeds``, grouped by sector, and each
group declares which tickers it serves. A feed that has moved is retried once
against whatever feed URL the site advertises in its ``<head>`` before being
recorded as failed.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..config import FeedConfig
from ..models import Article
from .base import (
    CollectionContext,
    HttpClient,
    Source,
    SourceError,
    find_feed_links,
    parse_feed_entries,
    site_root,
)


class RssSource(Source):
    name = "rss"

    def __init__(
        self, config: Dict[str, Any], client: HttpClient, feeds: List[FeedConfig] | None = None
    ) -> None:
        super().__init__(config, client)
        self.feeds = feeds or []

    def fetch(self, context: CollectionContext) -> List[Article]:
        articles: List[Article] = []
        for feed in self.feeds:
            if self.should_give_up():
                break
            self.attempted += 1
            content = self._get(feed)
            if content is None:
                continue
            self.succeeded += 1
            self.note_success()
            found = list(
                parse_feed_entries(
                    content,
                    feed_name=feed.name,
                    collector=self.name,
                    source_type=feed.source_type,
                    tickers_hint=[],
                )
            )
            if not found:
                self.record_note(feed.name, "feed parsed but contained no entries")
            for article in found:
                article.raw["feed_group"] = feed.group
                article.raw["feed_tickers"] = feed.tickers
                articles.append(article)
            self.client.sleep()
        return articles

    def _get(self, feed: FeedConfig) -> bytes | None:
        try:
            return self.client.get(feed.url).content
        except SourceError as exc:
            discovered = self._rediscover(feed)
            if discovered is not None:
                return discovered
            self.record_error(feed.name, exc)
            return None

    def _rediscover(self, feed: FeedConfig) -> bytes | None:
        """Publishers move feeds and leave the old path 404ing. Ask the site."""
        root = site_root(feed.url)
        if not root:
            return None
        try:
            page = self.client.get(root)
            candidates = find_feed_links(page.text, root)
        except SourceError:
            return None
        for candidate in candidates[:2]:
            if candidate == feed.url:
                continue
            try:
                content = self.client.get(candidate).content
            except SourceError:
                continue
            self.record_note(feed.name, f"feed URL moved; using {candidate}")
            return content
        return None
