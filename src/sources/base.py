"""Shared plumbing for every source.

The contract is deliberately tiny: a source has a ``name`` and a ``fetch()``
that returns :class:`~src.models.Article` objects. It must never raise for an
ordinary network problem — the collector treats an exception as a hard failure
of that source, and one blocked website must not take a daily run down with it.

Every source records its own errors and notes; ``main`` turns those into the
Run Diagnostics section of the report. A source is only ever described as
working if it actually returned items in a live run.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

import requests

from ..models import Article, SourceType

LOG = logging.getLogger(__name__)


class SourceError(Exception):
    """Raised for failures the collector should report but survive."""


@dataclass
class HttpClient:
    """Small requests wrapper with timeout, retries and a descriptive UA."""

    user_agent: str = "global-equity-intelligence/1.0"
    timeout: int = 20
    retries: int = 2
    delay: float = 0.6
    verify: bool = True
    session: Optional[requests.Session] = None

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = requests.Session()

    def default_headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept": (
                "application/rss+xml, application/xml, text/xml, "
                "application/json;q=0.9, text/html;q=0.8, */*;q=0.7"
            ),
            "Accept-Language": "en-IN,en;q=0.9",
        }

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        headers = self.default_headers()
        headers.update(kwargs.pop("headers", {}) or {})
        kwargs.setdefault("verify", self.verify)

        last_error: Optional[Exception] = None
        for attempt in range(self.retries + 1):
            try:
                response = self.session.get(url, headers=headers, timeout=self.timeout, **kwargs)
                response.raise_for_status()
                return response
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.retries:
                    # Linear backoff is plenty for feeds; we are not hammering.
                    time.sleep(self.delay * (attempt + 1))
        raise SourceError(f"HTTP request failed for {url}: {last_error}") from last_error

    def sleep(self) -> None:
        """Politeness pause between consecutive requests to one host."""
        if self.delay:
            time.sleep(self.delay)

    @classmethod
    def from_config(cls, http_config: Dict[str, Any]) -> "HttpClient":
        return cls(
            user_agent=str(http_config.get("user_agent", "global-equity-intelligence/1.0")),
            timeout=int(http_config.get("timeout_seconds", 20)),
            retries=int(http_config.get("max_retries", 2)),
            delay=float(http_config.get("per_request_delay_seconds", 0.6)),
            verify=bool(http_config.get("verify_tls", True)),
        )


@dataclass
class SourceOutcome:
    """What one source actually did, for Run Diagnostics."""

    name: str
    ok: bool = False
    attempted: int = 0
    succeeded: int = 0
    articles: int = 0
    duration_s: float = 0.0
    errors: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


class Source:
    """Base class for all collectors."""

    name = "source"

    # When a host is blocked or down, every request costs the full timeout and
    # every retry. Discovering that 26 times over is minutes of a run spent
    # learning the same thing, so a source gives up after this many failures
    # in a row and reports why.
    GIVE_UP_AFTER = 5

    def __init__(self, config: Dict[str, Any], client: HttpClient) -> None:
        self.config = config or {}
        self.client = client
        self.errors: List[str] = []
        # Notes are worth printing but are not failures — a query that
        # legitimately had no news today is not a broken source.
        self.notes: List[str] = []
        self.attempted = 0
        self.succeeded = 0
        self.consecutive_failures = 0
        self.gave_up = False

    def fetch(self, context: "CollectionContext") -> List[Article]:  # pragma: no cover
        raise NotImplementedError

    # -- circuit breaker --------------------------------------------------
    def should_give_up(self) -> bool:
        """True once this source has failed enough times to stop trying."""
        if self.gave_up:
            return True
        limit = int(self.config.get("give_up_after", self.GIVE_UP_AFTER))
        if self.consecutive_failures >= limit:
            self.gave_up = True
            self.record_note(
                "circuit breaker",
                f"{self.consecutive_failures} consecutive failures; "
                "skipping the rest of this source for this run",
            )
            return True
        return False

    def note_success(self) -> None:
        self.consecutive_failures = 0

    # -- helpers ---------------------------------------------------------
    def record_error(self, context: str, exc: Exception | str) -> None:
        message = f"{context}: {exc}"
        self.errors.append(message)
        self.consecutive_failures += 1
        # Only the first few failures are worth keeping; 200 copies of one
        # blocked host tells nobody anything extra.
        if len(self.errors) > 6:
            self.errors.pop(0)
        LOG.warning("%s: %s", self.name, message)

    def record_note(self, context: str, detail: str) -> None:
        message = f"{context}: {detail}"
        self.notes.append(message)
        LOG.info("%s: %s", self.name, message)

    def outcome(self, articles: int, duration: float) -> SourceOutcome:
        return SourceOutcome(
            name=self.name,
            ok=articles > 0 or (self.attempted > 0 and not self.errors),
            attempted=self.attempted,
            succeeded=self.succeeded,
            articles=articles,
            duration_s=round(duration, 2),
            errors=list(self.errors),
            notes=list(self.notes),
        )


@dataclass
class CollectionContext:
    """What the collectors need to know about this run."""

    profiles: List[Any]                      # List[CompanyProfile]
    queries: Dict[str, List[Any]] = field(default_factory=dict)
    since_days: int = 2
    until_days: int = 0
    max_articles_per_query: int = 25
    dry_run: bool = False

    @property
    def tickers(self) -> List[str]:
        return [p.ticker for p in self.profiles]


def parse_feed_entries(
    content: bytes | str,
    feed_name: str,
    collector: str,
    source_type: SourceType = SourceType.UNKNOWN_NEWS_SITE,
    query: str = "",
    tickers_hint: Optional[List[str]] = None,
) -> Iterable[Article]:
    """Parse RSS/Atom bytes into :class:`Article` objects.

    ``feedparser`` sets ``bozo`` on malformed feeds but usually still returns
    usable entries, so a parse warning is not by itself a reason to discard the
    feed.
    """
    import feedparser  # imported here so the module stays importable without it

    from ..normalize import parse_date

    parsed = feedparser.parse(content)
    for entry in getattr(parsed, "entries", []) or []:
        link = entry.get("link") or ""
        title = entry.get("title") or ""
        if not link or not title:
            continue
        published = (
            entry.get("published")
            or entry.get("updated")
            or entry.get("pubDate")
            or entry.get("dc_date")
        )
        description = entry.get("summary") or entry.get("description") or ""
        source_name = feed_name
        # Google News nests the real publisher here.
        nested = entry.get("source")
        if isinstance(nested, dict) and nested.get("title"):
            source_name = nested["title"]
        yield Article(
            title=title,
            url=link,
            source_name=source_name,
            source_type=source_type,
            summary=description,
            published=parse_date(published),
            collector=collector,
            query=query,
            tickers_hint=list(tickers_hint or []),
        )


class _FeedLinkExtractor(HTMLParser):
    """Collect <link rel="alternate" type="...rss|atom..."> hrefs."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "link":
            return
        mapping = {k.lower(): (v or "") for k, v in attrs}
        rel = mapping.get("rel", "").lower()
        ctype = mapping.get("type", "").lower()
        href = mapping.get("href", "")
        if href and "alternate" in rel and ("rss" in ctype or "atom" in ctype):
            self.hrefs.append(href)


def find_feed_links(html: str, base_url: str) -> List[str]:
    """Absolute feed URLs advertised by a page's <head>.

    Publishers move their feeds and leave the old path 404ing, so rather than
    hard-coding a guess we ask the site where its feed lives now.
    """
    parser = _FeedLinkExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML is expected
        pass
    out: List[str] = []
    for href in parser.hrefs:
        absolute = urljoin(base_url, href)
        if absolute.startswith(("http://", "https://")) and absolute not in out:
            out.append(absolute)
    return out


def site_root(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}/"


class _AnchorExtractor(HTMLParser):
    """Collect (href, text) pairs from a page - used for government listings."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: List[Tuple[str, str]] = []
        self._href: Optional[str] = None
        self._text: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() == "a":
            mapping = {k.lower(): (v or "") for k, v in attrs}
            self._href = mapping.get("href") or None
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href is not None:
            text = " ".join("".join(self._text).split())
            if text:
                self.links.append((self._href, text))
            self._href = None
            self._text = []


def extract_links(html: str, base_url: str) -> List[Tuple[str, str]]:
    """Absolute (url, anchor text) pairs from an HTML listing page."""
    parser = _AnchorExtractor()
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001 - malformed HTML is expected
        pass
    out: List[Tuple[str, str]] = []
    for href, text in parser.links:
        absolute = urljoin(base_url, href)
        if absolute.startswith(("http://", "https://")):
            out.append((absolute, text))
    return out
