"""Article -> Event clustering.

This is the design principle of the whole system: the primary object is the
Event, not the article. A Reuters story, an Economic Times write-up, an NSE
filing and the company's own press release about one export order are ONE
event with four sources — not four intelligence items.

Two articles join the same cluster when they share affected companies, fall
inside a short time window, and their headlines are similar enough. Similarity
alone is too blunt for short exchange-announcement headlines, so shared event
categories, same-day publication and an official filing each add a bounded
bonus.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .classify import Classification
from .matching import title_similarity
from .models import (
    Article,
    Event,
    EventCategory,
    EventSource,
    Relationship,
    SourceType,
    utc_now,
)
from .relationship import StockLink

# Bonuses applied on top of raw title similarity.
CATEGORY_BONUS = 0.12
SAME_DAY_BONUS = 0.08
OFFICIAL_BONUS = 0.08
SHARED_DIRECT_BONUS = 0.06
MAX_BONUS = 0.30


@dataclass
class MatchedArticle:
    """An article after matching: which companies, how, and what kind of event."""

    article: Article
    links: List[StockLink] = field(default_factory=list)
    classification: Optional[Classification] = None

    @property
    def tickers(self) -> Set[str]:
        return {link.ticker for link in self.links}

    @property
    def direct_tickers(self) -> Set[str]:
        return {link.ticker for link in self.links if link.is_direct}

    @property
    def categories(self) -> List[EventCategory]:
        return list(self.classification.categories) if self.classification else []

    @property
    def event_day(self) -> date:
        if self.article.published:
            return self.article.published.date()
        return self.article.collected_at.date()


@dataclass
class Cluster:
    """A group of articles believed to describe one development."""

    articles: List[MatchedArticle] = field(default_factory=list)

    @property
    def lead(self) -> MatchedArticle:
        """The article that best represents the event.

        Official filings win, then source quality, then the longest headline
        (short wire headlines lose detail the report needs).
        """
        return max(
            self.articles,
            key=lambda m: (
                m.article.is_official,
                _quality_rank(m.article.source_type),
                len(m.article.title),
            ),
        )

    @property
    def tickers(self) -> Set[str]:
        out: Set[str] = set()
        for matched in self.articles:
            out |= matched.tickers
        return out

    @property
    def categories(self) -> List[EventCategory]:
        """Union of the members' categories, OTHER only if nothing else fits."""
        ordered: List[EventCategory] = []
        for matched in self.articles:
            for category in matched.categories:
                if category not in ordered:
                    ordered.append(category)
        specific = [c for c in ordered if c is not EventCategory.OTHER]
        return specific or ordered

    @property
    def event_day(self) -> date:
        days = [m.event_day for m in self.articles]
        return min(days)

    def source_domains(self) -> Set[str]:
        return {m.article.source_domain for m in self.articles if m.article.source_domain}


def _quality_rank(source_type: SourceType) -> int:
    order = {
        SourceType.COMPANY_EXCHANGE_FILING: 10,
        SourceType.REGULATOR: 10,
        SourceType.COMPANY_IR: 9,
        SourceType.REUTERS: 8,
        SourceType.MAJOR_FINANCIAL_PRESS: 7,
        SourceType.ESTABLISHED_NEWSPAPER: 6,
        SourceType.SPECIALIZED_TRADE_PUBLICATION: 5,
        SourceType.UNKNOWN_NEWS_SITE: 3,
        SourceType.BLOG: 2,
        SourceType.SOCIAL_MEDIA: 1,
    }
    return order.get(source_type, 3)


def similarity_with_bonuses(a: MatchedArticle, b: MatchedArticle) -> Tuple[float, List[str]]:
    """Blended similarity plus the reasons any bonus was applied."""
    base = title_similarity(a.article.title, b.article.title)
    bonus = 0.0
    reasons: List[str] = []

    shared_categories = set(a.categories) & set(b.categories)
    meaningful = shared_categories - {EventCategory.OTHER}
    if meaningful:
        bonus += CATEGORY_BONUS
        reasons.append(f"same event type ({sorted(c.value for c in meaningful)[0]})")

    if a.event_day == b.event_day:
        bonus += SAME_DAY_BONUS
        reasons.append("same day")

    if a.article.is_official or b.article.is_official:
        bonus += OFFICIAL_BONUS
        reasons.append("official filing in the group")

    if a.direct_tickers and a.direct_tickers & b.direct_tickers:
        bonus += SHARED_DIRECT_BONUS
        reasons.append("same company directly involved")

    return base + min(bonus, MAX_BONUS), reasons


def can_cluster(
    a: MatchedArticle, b: MatchedArticle, threshold: float, window_days: int
) -> Tuple[bool, float, List[str]]:
    """Gate first, then score. The gate is what stops nonsense merges."""
    if not (a.tickers & b.tickers):
        return False, 0.0, []
    if abs((a.event_day - b.event_day).days) > window_days:
        return False, 0.0, []

    # Two articles that each name a company directly must name the SAME one.
    if a.direct_tickers and b.direct_tickers and not (a.direct_tickers & b.direct_tickers):
        return False, 0.0, []

    score, reasons = similarity_with_bonuses(a, b)
    return score >= threshold, score, reasons


def cluster_articles(
    matched: Sequence[MatchedArticle],
    threshold: float = 0.62,
    window_days: int = 3,
) -> List[Cluster]:
    """Greedy single-link clustering, deterministic in input order."""
    ordered = sorted(
        matched,
        key=lambda m: (
            not m.article.is_official,
            -_quality_rank(m.article.source_type),
            m.article.published.timestamp() if m.article.published else 0.0,
            m.article.title,
        ),
    )

    clusters: List[Cluster] = []
    for item in ordered:
        best: Optional[Cluster] = None
        best_score = 0.0
        for cluster in clusters:
            for member in cluster.articles:
                ok, score, _ = can_cluster(item, member, threshold, window_days)
                if ok and score > best_score:
                    best, best_score = cluster, score
                    break
        if best is not None:
            best.articles.append(item)
        else:
            clusters.append(Cluster(articles=[item]))
    return clusters


def make_event_id(cluster: Cluster, sequence: int, year: Optional[int] = None) -> str:
    """``EVENT-<TICKER>-<YEAR>-<NNNN>``; GLOBAL when several stocks are hit."""
    direct = sorted({t for m in cluster.articles for t in m.direct_tickers})
    tickers = sorted(cluster.tickers)
    if len(direct) == 1:
        scope = direct[0]
    elif len(tickers) == 1:
        scope = tickers[0]
    else:
        scope = "GLOBAL"
    stamp = year or cluster.event_day.year
    return f"EVENT-{scope}-{stamp}-{sequence:04d}"


def build_event(
    cluster: Cluster,
    event_id: str,
    quality_map: Optional[Dict[str, int]] = None,
    now: Optional[datetime] = None,
) -> Event:
    """Turn a cluster into an Event (impact scoring happens separately)."""
    lead = cluster.lead
    moment = now or utc_now()
    sources = [
        EventSource.from_article(
            m.article, quality=(quality_map or {}).get(m.article.source_type.value, 3)
        )
        for m in sorted(
            cluster.articles,
            key=lambda m: (-_quality_rank(m.article.source_type), m.article.title),
        )
    ]
    event = Event(
        event_id=event_id,
        title=lead.article.title,
        summary=lead.article.summary[:600],
        event_date=cluster.event_day,
        event_types=cluster.categories[:5],
        sources=sources,
        primary_source=sources[0].source_name if sources else "",
        cluster_key=_cluster_key(cluster),
        first_seen=moment,
        last_updated=moment,
        is_international=any(link.international for m in cluster.articles for link in m.links),
    )
    event.record("created", f"{len(sources)} source(s) from {len(cluster.source_domains())} domain(s)")
    return event


def _cluster_key(cluster: Cluster) -> str:
    """A stable-ish key used to recognise the same story on a later run."""
    from .matching import canonical_tokens

    tickers = "+".join(sorted(cluster.tickers)) or "NONE"
    categories = "+".join(sorted({c.value for c in cluster.categories[:2]})) or "OTHER"
    words = canonical_tokens(cluster.lead.article.title)[:6]
    return f"{tickers}|{categories}|{'-'.join(sorted(words))}"
