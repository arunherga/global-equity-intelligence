"""Core domain model for the global equity intelligence system.

The primary object in this system is the :class:`Event`, not the article.
Many articles from many sources may describe one event; the pipeline collapses
them into a single Event that carries one impact record per affected stock.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional

# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------


class Relationship(str, Enum):
    """Strength of the link between an event and a watchlist company."""

    DIRECT = "DIRECT"
    INDIRECT_STRONG = "INDIRECT_STRONG"
    INDIRECT = "INDIRECT"
    SECTOR = "SECTOR"
    MACRO = "MACRO"
    WEAK = "WEAK"

    @property
    def rank(self) -> int:
        return _RELATIONSHIP_RANK[self]

    def stronger_than(self, other: "Relationship") -> bool:
        return self.rank > other.rank

    @staticmethod
    def strongest(values: Iterable["Relationship"]) -> "Relationship":
        best = Relationship.WEAK
        for value in values:
            if value.rank > best.rank:
                best = value
        return best


_RELATIONSHIP_RANK: Dict[Relationship, int] = {
    Relationship.WEAK: 0,
    Relationship.MACRO: 1,
    Relationship.SECTOR: 2,
    Relationship.INDIRECT: 3,
    Relationship.INDIRECT_STRONG: 4,
    Relationship.DIRECT: 5,
}


class ExposureType(str, Enum):
    """How a company is exposed to a piece of news."""

    COMPANY = "COMPANY"
    SUBSIDIARY = "SUBSIDIARY"
    PRODUCT = "PRODUCT"
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    COMPETITOR = "COMPETITOR"
    INDUSTRY = "INDUSTRY"
    COMMODITY = "COMMODITY"
    CURRENCY = "CURRENCY"
    REGULATION = "REGULATION"
    GEOGRAPHY = "GEOGRAPHY"
    MACRO = "MACRO"
    TECHNOLOGY = "TECHNOLOGY"


class EventCategory(str, Enum):
    EARNINGS = "EARNINGS"
    GUIDANCE = "GUIDANCE"
    ORDER_WIN = "ORDER_WIN"
    ORDER_LOSS = "ORDER_LOSS"
    CONTRACT_WIN = "CONTRACT_WIN"
    CONTRACT_LOSS = "CONTRACT_LOSS"
    NEW_CUSTOMER = "NEW_CUSTOMER"
    CUSTOMER_LOSS = "CUSTOMER_LOSS"
    EXPANSION = "EXPANSION"
    NEW_PLANT = "NEW_PLANT"
    CAPACITY_EXPANSION = "CAPACITY_EXPANSION"
    CAPEX = "CAPEX"
    MERGER = "MERGER"
    ACQUISITION = "ACQUISITION"
    DIVESTITURE = "DIVESTITURE"
    INVESTMENT = "INVESTMENT"
    PARTNERSHIP = "PARTNERSHIP"
    NEW_PRODUCT = "NEW_PRODUCT"
    PRODUCT_DELAY = "PRODUCT_DELAY"
    PRODUCT_FAILURE = "PRODUCT_FAILURE"
    EXPORT_ORDER = "EXPORT_ORDER"
    EXPORT_RESTRICTION = "EXPORT_RESTRICTION"
    IMPORT_RESTRICTION = "IMPORT_RESTRICTION"
    REGULATORY = "REGULATORY"
    LEGAL = "LEGAL"
    LITIGATION = "LITIGATION"
    MANAGEMENT_CHANGE = "MANAGEMENT_CHANGE"
    PROMOTER_ACTIVITY = "PROMOTER_ACTIVITY"
    INSIDER_BUYING = "INSIDER_BUYING"
    INSIDER_SELLING = "INSIDER_SELLING"
    SHARE_BUYBACK = "SHARE_BUYBACK"
    DIVIDEND = "DIVIDEND"
    FUNDRAISING = "FUNDRAISING"
    DEBT = "DEBT"
    CREDIT_RATING = "CREDIT_RATING"
    SUPPLY_CHAIN = "SUPPLY_CHAIN"
    RAW_MATERIAL = "RAW_MATERIAL"
    COMMODITY = "COMMODITY"
    COMPETITOR = "COMPETITOR"
    CUSTOMER = "CUSTOMER"
    CYBERSECURITY = "CYBERSECURITY"
    FRAUD = "FRAUD"
    GOVERNMENT_POLICY = "GOVERNMENT_POLICY"
    TENDER = "TENDER"
    TARIFF = "TARIFF"
    TRADE_POLICY = "TRADE_POLICY"
    GEOPOLITICAL = "GEOPOLITICAL"
    MACRO = "MACRO"
    INTEREST_RATE = "INTEREST_RATE"
    CURRENCY = "CURRENCY"
    INDUSTRY_DEMAND = "INDUSTRY_DEMAND"
    TECHNOLOGY = "TECHNOLOGY"
    ANALYST_ACTION = "ANALYST_ACTION"
    OTHER = "OTHER"


class Direction(str, Enum):
    """Likely direction of effect. Deliberately separate from importance."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    MIXED = "MIXED"
    NEUTRAL = "NEUTRAL"
    UNCERTAIN = "UNCERTAIN"


class BusinessImpact(str, Enum):
    REVENUE = "REVENUE"
    MARGIN = "MARGIN"
    DEMAND = "DEMAND"
    PRICING = "PRICING"
    INPUT_COST = "INPUT_COST"
    SUPPLY = "SUPPLY"
    CAPACITY = "CAPACITY"
    EXPORTS = "EXPORTS"
    IMPORTS = "IMPORTS"
    CUSTOMERS = "CUSTOMERS"
    COMPETITION = "COMPETITION"
    REGULATION = "REGULATION"
    BALANCE_SHEET = "BALANCE_SHEET"
    DEBT = "DEBT"
    CASH_FLOW = "CASH_FLOW"
    MANAGEMENT = "MANAGEMENT"
    PRODUCT = "PRODUCT"
    GEOGRAPHY = "GEOGRAPHY"
    REPUTATION = "REPUTATION"


class TimeHorizon(str, Enum):
    IMMEDIATE = "IMMEDIATE"          # 0-7 days
    SHORT_TERM = "SHORT_TERM"        # 1 week - 3 months
    MEDIUM_TERM = "MEDIUM_TERM"      # 3 - 12 months
    LONG_TERM = "LONG_TERM"          # > 12 months
    UNKNOWN = "UNKNOWN"


class SourceType(str, Enum):
    """Used for source-quality scoring; see config.yaml -> source_quality."""

    COMPANY_EXCHANGE_FILING = "company_exchange_filing"
    REGULATOR = "regulator"
    COMPANY_IR = "company_ir"
    REUTERS = "reuters"
    MAJOR_FINANCIAL_PRESS = "major_financial_press"
    ESTABLISHED_NEWSPAPER = "established_newspaper"
    SPECIALIZED_TRADE_PUBLICATION = "specialized_trade_publication"
    UNKNOWN_NEWS_SITE = "unknown_news_site"
    BLOG = "blog"
    SOCIAL_MEDIA = "social_media"


IMPACT_SCORE_MIN = 0
IMPACT_SCORE_MAX = 15


def impact_band(score: int) -> str:
    """Human label for an impact score in the 0-15 range."""
    if score <= 2:
        return "NOISE"
    if score <= 4:
        return "LOW"
    if score <= 7:
        return "RELEVANT"
    if score <= 10:
        return "HIGH"
    if score <= 12:
        return "VERY_HIGH"
    return "CRITICAL"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_date(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def stable_id(*parts: str) -> str:
    joined = "||".join(p or "" for p in parts)
    return hashlib.sha1(joined.encode("utf-8", "ignore")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Article
# --------------------------------------------------------------------------


@dataclass
class Article:
    """A single retrieved item. Articles are inputs, not the primary object."""

    title: str
    url: str
    source_name: str = ""
    source_domain: str = ""
    source_type: SourceType = SourceType.UNKNOWN_NEWS_SITE
    summary: str = ""
    published: Optional[datetime] = None
    collected_at: datetime = field(default_factory=utc_now)
    collector: str = ""
    query: str = ""
    language: str = "en"
    tickers_hint: List[str] = field(default_factory=list)
    is_official: bool = False
    article_id: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.article_id:
            self.article_id = stable_id(self.canonical_key())

    def canonical_key(self) -> str:
        """Key used for exact-duplicate detection."""
        return f"{self.source_domain}|{normalize_title(self.title)}"

    @property
    def text(self) -> str:
        return f"{self.title}. {self.summary}".strip()

    @property
    def published_date(self) -> Optional[date]:
        return self.published.date() if self.published else None

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["source_type"] = self.source_type.value
        data["published"] = _iso(self.published)
        data["collected_at"] = _iso(self.collected_at)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Article":
        return cls(
            title=data.get("title", ""),
            url=data.get("url", ""),
            source_name=data.get("source_name", ""),
            source_domain=data.get("source_domain", ""),
            source_type=SourceType(data.get("source_type", SourceType.UNKNOWN_NEWS_SITE.value)),
            summary=data.get("summary", ""),
            published=_parse_iso(data.get("published")),
            collected_at=_parse_iso(data.get("collected_at")) or utc_now(),
            collector=data.get("collector", ""),
            query=data.get("query", ""),
            language=data.get("language", "en"),
            tickers_hint=list(data.get("tickers_hint", [])),
            is_official=bool(data.get("is_official", False)),
            article_id=data.get("article_id", ""),
            raw=dict(data.get("raw", {})),
        )


_TITLE_NOISE = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalize_title(title: str) -> str:
    """Lowercased, punctuation-stripped title used for dedup and clustering."""
    text = (title or "").lower()
    text = text.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    text = _TITLE_NOISE.sub(" ", text)
    return _WS.sub(" ", text).strip()


# --------------------------------------------------------------------------
# Exposure / impact records
# --------------------------------------------------------------------------


@dataclass
class ExposureMatch:
    """Why an article was considered relevant to a company."""

    exposure_type: ExposureType
    term: str
    relationship: Relationship
    weight: float = 1.0
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "exposure_type": self.exposure_type.value,
            "term": self.term,
            "relationship": self.relationship.value,
            "weight": self.weight,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExposureMatch":
        return cls(
            exposure_type=ExposureType(data["exposure_type"]),
            term=data.get("term", ""),
            relationship=Relationship(data.get("relationship", Relationship.WEAK.value)),
            weight=float(data.get("weight", 1.0)),
            detail=data.get("detail", ""),
        )


@dataclass
class PriceReaction:
    """Reserved now, populated later by a market-data integration."""

    price_at_event: Optional[float] = None
    return_1d: Optional[float] = None
    return_3d: Optional[float] = None
    return_5d: Optional[float] = None
    return_10d: Optional[float] = None
    return_20d: Optional[float] = None
    return_60d: Optional[float] = None
    volume_change: Optional[float] = None
    gap_percentage: Optional[float] = None
    provider: Optional[str] = None
    updated_at: Optional[str] = None

    def is_empty(self) -> bool:
        return all(getattr(self, f) is None for f in self.__dataclass_fields__)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PriceReaction":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class StockImpact:
    """One watchlist company's view of an event."""

    ticker: str
    relationship: Relationship
    impact_score: int = 0
    direction: Direction = Direction.UNCERTAIN
    confidence: float = 0.0
    business_impacts: List[BusinessImpact] = field(default_factory=list)
    time_horizon: TimeHorizon = TimeHorizon.UNKNOWN
    exposures: List[ExposureMatch] = field(default_factory=list)
    score_reasons: List[str] = field(default_factory=list)
    direction_reasons: List[str] = field(default_factory=list)
    confidence_reasons: List[str] = field(default_factory=list)
    watch_next: List[str] = field(default_factory=list)
    why_it_matters: str = ""
    ai_analysis: Optional[Dict[str, Any]] = None

    @property
    def band(self) -> str:
        return impact_band(self.impact_score)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "relationship": self.relationship.value,
            "impact_score": self.impact_score,
            "impact_band": self.band,
            "direction": self.direction.value,
            "confidence": round(self.confidence, 3),
            "business_impacts": [b.value for b in self.business_impacts],
            "time_horizon": self.time_horizon.value,
            "exposures": [e.to_dict() for e in self.exposures],
            "score_reasons": list(self.score_reasons),
            "direction_reasons": list(self.direction_reasons),
            "confidence_reasons": list(self.confidence_reasons),
            "watch_next": list(self.watch_next),
            "why_it_matters": self.why_it_matters,
            "ai_analysis": self.ai_analysis,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StockImpact":
        return cls(
            ticker=data["ticker"],
            relationship=Relationship(data.get("relationship", Relationship.WEAK.value)),
            impact_score=int(data.get("impact_score", 0)),
            direction=Direction(data.get("direction", Direction.UNCERTAIN.value)),
            confidence=float(data.get("confidence", 0.0)),
            business_impacts=[BusinessImpact(b) for b in data.get("business_impacts", [])],
            time_horizon=TimeHorizon(data.get("time_horizon", TimeHorizon.UNKNOWN.value)),
            exposures=[ExposureMatch.from_dict(e) for e in data.get("exposures", [])],
            score_reasons=list(data.get("score_reasons", [])),
            direction_reasons=list(data.get("direction_reasons", [])),
            confidence_reasons=list(data.get("confidence_reasons", [])),
            watch_next=list(data.get("watch_next", [])),
            why_it_matters=data.get("why_it_matters", ""),
            ai_analysis=data.get("ai_analysis"),
        )


@dataclass
class EventSource:
    """A source article attached to an event (compact projection)."""

    title: str
    url: str
    source_name: str
    source_domain: str
    source_type: SourceType
    published: Optional[datetime] = None
    is_official: bool = False
    quality: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "source_name": self.source_name,
            "source_domain": self.source_domain,
            "source_type": self.source_type.value,
            "published": _iso(self.published),
            "is_official": self.is_official,
            "quality": self.quality,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventSource":
        return cls(
            title=data.get("title", ""),
            url=data.get("url", ""),
            source_name=data.get("source_name", ""),
            source_domain=data.get("source_domain", ""),
            source_type=SourceType(data.get("source_type", SourceType.UNKNOWN_NEWS_SITE.value)),
            published=_parse_iso(data.get("published")),
            is_official=bool(data.get("is_official", False)),
            quality=int(data.get("quality", 0)),
        )

    @classmethod
    def from_article(cls, article: Article, quality: int = 0) -> "EventSource":
        return cls(
            title=article.title,
            url=article.url,
            source_name=article.source_name,
            source_domain=article.source_domain,
            source_type=article.source_type,
            published=article.published,
            is_official=article.is_official,
            quality=quality,
        )


@dataclass
class EventHistoryEntry:
    timestamp: str
    change: str
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventHistoryEntry":
        return cls(
            timestamp=data.get("timestamp", ""),
            change=data.get("change", ""),
            detail=data.get("detail", ""),
        )


@dataclass
class Event:
    """The primary object: one real-world development, many source articles."""

    event_id: str
    title: str
    event_date: Optional[date]
    event_types: List[EventCategory] = field(default_factory=list)
    stocks: Dict[str, StockImpact] = field(default_factory=dict)
    sources: List[EventSource] = field(default_factory=list)
    summary: str = ""
    cluster_key: str = ""
    primary_source: str = ""
    first_seen: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    history: List[EventHistoryEntry] = field(default_factory=list)
    price_reaction: Dict[str, PriceReaction] = field(default_factory=dict)
    is_international: bool = False
    tags: List[str] = field(default_factory=list)

    # -- convenience -----------------------------------------------------
    @property
    def article_count(self) -> int:
        return len(self.sources)

    @property
    def source_count(self) -> int:
        return len({s.source_domain for s in self.sources if s.source_domain})

    @property
    def max_impact(self) -> int:
        return max((s.impact_score for s in self.stocks.values()), default=0)

    @property
    def tickers(self) -> List[str]:
        return sorted(self.stocks)

    def has_official_source(self) -> bool:
        return any(s.is_official for s in self.sources)

    def top_impact(self) -> Optional[StockImpact]:
        if not self.stocks:
            return None
        return max(self.stocks.values(), key=lambda s: s.impact_score)

    def record(self, change: str, detail: str = "") -> None:
        self.history.append(
            EventHistoryEntry(timestamp=_iso(utc_now()) or "", change=change, detail=detail)
        )
        self.last_updated = utc_now()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "summary": self.summary,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "event_types": [c.value for c in self.event_types],
            "stocks": {t: s.to_dict() for t, s in self.stocks.items()},
            "sources": [s.to_dict() for s in self.sources],
            "article_count": self.article_count,
            "source_count": self.source_count,
            "primary_source": self.primary_source,
            "cluster_key": self.cluster_key,
            "is_international": self.is_international,
            "tags": list(self.tags),
            "first_seen": _iso(self.first_seen),
            "last_updated": _iso(self.last_updated),
            "event_history": [h.to_dict() for h in self.history],
            "price_reaction": {t: p.to_dict() for t, p in self.price_reaction.items()},
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        return cls(
            event_id=data["event_id"],
            title=data.get("title", ""),
            summary=data.get("summary", ""),
            event_date=_parse_date(data.get("event_date")),
            event_types=[EventCategory(c) for c in data.get("event_types", [])],
            stocks={t: StockImpact.from_dict(s) for t, s in (data.get("stocks") or {}).items()},
            sources=[EventSource.from_dict(s) for s in data.get("sources", [])],
            primary_source=data.get("primary_source", ""),
            cluster_key=data.get("cluster_key", ""),
            is_international=bool(data.get("is_international", False)),
            tags=list(data.get("tags", [])),
            first_seen=_parse_iso(data.get("first_seen")),
            last_updated=_parse_iso(data.get("last_updated")),
            history=[EventHistoryEntry.from_dict(h) for h in data.get("event_history", [])],
            price_reaction={
                t: PriceReaction.from_dict(p) for t, p in (data.get("price_reaction") or {}).items()
            },
        )


# --------------------------------------------------------------------------
# Run bookkeeping
# --------------------------------------------------------------------------


@dataclass
class SourceDiagnostic:
    """Per-source outcome. A failure here must never stop the run."""

    source: str
    ok: bool
    attempted: int = 0
    succeeded: int = 0
    articles: int = 0
    duration_s: float = 0.0
    errors: List[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunStats:
    articles_scanned: int = 0
    unique_articles: int = 0
    events_detected: int = 0
    relevant_events: int = 0
    high_impact_events: int = 0
    critical_events: int = 0
    international_events: int = 0
    official_announcements: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    run_date: date
    started_at: datetime
    finished_at: Optional[datetime] = None
    stats: RunStats = field(default_factory=RunStats)
    diagnostics: List[SourceDiagnostic] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    tickers: List[str] = field(default_factory=list)
    dry_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run_date": self.run_date.isoformat(),
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "dry_run": self.dry_run,
            "tickers": list(self.tickers),
            "stats": self.stats.to_dict(),
            "diagnostics": [d.to_dict() for d in self.diagnostics],
            "events": [e.to_dict() for e in self.events],
        }
