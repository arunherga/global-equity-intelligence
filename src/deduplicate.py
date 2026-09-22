"""Article deduplication.

The same story arrives many times: the wire copy, three papers running it, and
the aggregator returning it under four different queries. Deduplication here is
about *identical or near-identical articles*; collapsing genuinely different
articles that describe the same development is a separate, later step
(:mod:`src.event_cluster`).

Three passes, cheapest first:
  1. canonical URL
  2. exact normalised title from the same publisher
  3. near-duplicate title within a short window
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Dict, Iterable, List, Optional, Tuple

from .matching import fold, title_similarity
from .models import Article, SourceType, normalize_title


@dataclass
class DedupeResult:
    articles: List[Article] = field(default_factory=list)
    duplicates: Dict[str, List[str]] = field(default_factory=dict)
    removed: int = 0

    @property
    def kept(self) -> int:
        return len(self.articles)


def _quality(article: Article, quality_map: Optional[Dict[str, int]]) -> int:
    if not quality_map:
        return 0
    return quality_map.get(article.source_type.value, 3)


def _prefer(a: Article, b: Article, quality_map: Optional[Dict[str, int]]) -> Article:
    """Keep the better copy: official first, then quality, then the longer text."""
    if a.is_official != b.is_official:
        return a if a.is_official else b
    qa, qb = _quality(a, quality_map), _quality(b, quality_map)
    if qa != qb:
        return a if qa > qb else b
    if (a.published is None) != (b.published is None):
        return a if a.published is not None else b
    if len(a.summary) != len(b.summary):
        return a if len(a.summary) > len(b.summary) else b
    return a


def deduplicate(
    articles: Iterable[Article],
    similarity_threshold: float = 0.88,
    window_days: int = 5,
    quality_map: Optional[Dict[str, int]] = None,
) -> DedupeResult:
    """Collapse duplicate articles, keeping the best copy of each."""
    items = [a for a in articles if a.title and a.url]
    # Deterministic order: official sources first, then newest.
    items.sort(
        key=lambda a: (
            not a.is_official,
            -_quality(a, quality_map),
            a.published.timestamp() if a.published else 0.0,
        ),
        reverse=False,
    )

    by_url: Dict[str, Article] = {}
    by_title_source: Dict[Tuple[str, str], Article] = {}
    kept: List[Article] = []
    duplicates: Dict[str, List[str]] = {}
    removed = 0

    def record(winner: Article, loser: Article) -> None:
        duplicates.setdefault(winner.article_id, []).append(loser.url)

    for article in items:
        url_key = article.url.lower()
        if url_key in by_url:
            existing = by_url[url_key]
            winner = _prefer(existing, article, quality_map)
            if winner is article:
                kept[kept.index(existing)] = article
                by_url[url_key] = article
                record(article, existing)
            else:
                record(existing, article)
            removed += 1
            continue

        title_key = (normalize_title(article.title), article.source_domain.lower())
        if title_key[0] and title_key in by_title_source:
            existing = by_title_source[title_key]
            winner = _prefer(existing, article, quality_map)
            if winner is article:
                kept[kept.index(existing)] = article
                by_title_source[title_key] = article
                by_url[url_key] = article
                record(article, existing)
            else:
                record(existing, article)
            removed += 1
            continue

        near = _find_near_duplicate(article, kept, similarity_threshold, window_days)
        if near is not None:
            winner = _prefer(near, article, quality_map)
            if winner is article:
                kept[kept.index(near)] = article
                record(article, near)
            else:
                record(near, article)
            removed += 1
            continue

        kept.append(article)
        by_url[url_key] = article
        if title_key[0]:
            by_title_source[title_key] = article

    return DedupeResult(articles=kept, duplicates=duplicates, removed=removed)


def _find_near_duplicate(
    article: Article, kept: List[Article], threshold: float, window_days: int
) -> Optional[Article]:
    """A near-identical headline from the SAME publisher is the same article.

    Cross-publisher near-duplicates are deliberately left alone. Three papers
    running the same wire copy is evidence — it becomes one Event with three
    sources, and "three or more independent sources" is a scoring signal.
    Deleting them here would destroy that.
    """
    for existing in reversed(kept[-400:]):  # recent window keeps this linear enough
        if existing.source_domain.lower() != article.source_domain.lower():
            continue
        if not _within_window(article, existing, window_days):
            continue
        if title_similarity(article.title, existing.title) >= threshold:
            return existing
    return None


def _within_window(a: Article, b: Article, window_days: int) -> bool:
    if a.published is None or b.published is None:
        return True
    return abs((a.published - b.published).total_seconds()) <= window_days * 86400
