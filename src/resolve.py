"""Turn aggregator redirect links into the publisher's own URL.

Google News RSS does not hand out the article's real address. It returns an
opaque ``news.google.com/rss/articles/CBMi...`` link that only redirects when
followed. Those links are unreadable in a report, impossible to deduplicate
against the same story arriving from a direct feed, and they hide which
publisher you are about to open.

Resolving every collected item would mean hundreds of requests, so this runs
late — only for the articles that will actually appear in the report — and it
fails soft: an unresolvable link is left exactly as it was.

Everything below is the result of two live failures in the sibling project:
the first "fix" resolved links to a 16-pixel favicon, the second resolved
nothing at all. Hence the strict article test, the repeated-target guard, and
:func:`describe_page`, which reports what was actually received so the next
change can be made from evidence rather than a third guess.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

from .models import Article
from .normalize import canonical_url, domain_of

LOG = logging.getLogger(__name__)

# Hosts whose links are redirects rather than articles.
AGGREGATOR_HOSTS = ("news.google.com", "news.bing.com", "bing.com")

# Stop after this many failures in a row rather than spending the whole
# budget discovering the same thing hundreds of times.
GIVE_UP_AFTER = 20


def needs_resolution(url: str) -> bool:
    host = domain_of(url)
    return any(host == h or host.endswith("." + h) for h in AGGREGATOR_HOSTS)


# Anything Google serves as page furniture, plus the share widgets that sit on
# every article.
_BLOCKED_SUFFIXES = (
    "googleusercontent.com", "gstatic.com", "ggpht.com", "googleapis.com",
    "googletagmanager.com", "google-analytics.com", "doubleclick.net",
    "youtube.com", "youtu.be", "blogger.com", "schema.org", "w3.org",
    "facebook.com", "twitter.com", "x.com", "linkedin.com", "whatsapp.com",
    "pinterest.com", "reddit.com", "instagram.com", "t.me", "telegram.me",
)

_ASSET_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".avif",
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom",
    ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".wav",
)

# Google's image sizing suffix, e.g. the "=w16" that produced the favicon.
_IMAGE_SIZING = re.compile(r"=[wshc]\d+(-[a-z0-9]+)*$", re.I)

_BODY_PATTERNS = (
    re.compile(r'data-n-au="([^"]+)"'),
    re.compile(r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+url=([^"'>\s]+)""", re.I),
    re.compile(r'<link[^>]+rel=["\']?canonical["\']?[^>]+href="([^"]+)"', re.I),
)
_ANY_LINK = re.compile(r'href="(https?://[^"]+)"')


def _blocked_host(host: str) -> bool:
    host = (host or "").lower()
    if not host:
        return True
    # No legitimate publisher has "google" in its hostname; this catches every
    # Google asset domain at once, present and future.
    if "google" in host:
        return True
    return any(host == s or host.endswith("." + s) for s in _BLOCKED_SUFFIXES)


def looks_like_article(url: str) -> bool:
    """Is this plausibly a link to a story, rather than an asset or a widget?

    Deliberately strict. Keeping an unreadable aggregator link is a small
    annoyance; replacing it with a favicon is worse, because the article is
    then unrecoverable and every such link collapses to the same identity.
    """
    if not url or not url.startswith(("http://", "https://")):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if _blocked_host(parts.hostname or ""):
        return False
    path = (parts.path or "/").rstrip("/")
    if not path or path == "/":
        return False
    if path.lower().endswith(_ASSET_SUFFIXES):
        return False
    if _IMAGE_SIZING.search(url):
        return False
    # An article path carries a slug or an id; a tracking pixel does not.
    return len(path.strip("/")) >= 8


def _from_body(html: str) -> str:
    """Pull the destination out of an aggregator's redirect page."""
    if not html:
        return ""
    for pattern in _BODY_PATTERNS:
        match = pattern.search(html)
        if match and looks_like_article(match.group(1).strip()):
            return match.group(1).strip()
    for match in _ANY_LINK.finditer(html[:200000]):
        candidate = match.group(1)
        if not needs_resolution(candidate) and looks_like_article(candidate):
            return candidate
    return ""


def describe_page(html: str, final_url: str) -> str:
    """A short fingerprint of a page that could not be resolved."""
    markers = []
    for label, needle in (
        ("data-n-au", "data-n-au"),
        ("meta-refresh", 'http-equiv="refresh"'),
        ("canonical", 'rel="canonical"'),
        ("consent", "consent"),
        ("needs-js", "enable JavaScript"),
        ("c-wiz", "<c-wiz"),
    ):
        if needle.lower() in html.lower():
            markers.append(label)
    external = len(_ANY_LINK.findall(html[:200000]))
    host = (urlsplit(final_url).hostname or "?") if final_url else "?"
    return (
        f"landed on {host}, {len(html)} bytes, {external} links, "
        f"markers: {', '.join(markers) or 'none'}"
    )


def resolve_one(url: str, client, diagnostics: Optional[List[str]] = None) -> str:
    """Follow ``url`` and return where it landed, or ``url`` unchanged."""
    try:
        response = client.get(url)
    except Exception as exc:  # noqa: BLE001 - never fail a run over a link
        LOG.info("could not resolve %s: %s", url[:80], exc)
        if diagnostics is not None:
            diagnostics.append(f"request failed: {exc}")
        return url
    final = str(getattr(response, "url", "") or "")
    if final and not needs_resolution(final):
        return final
    html = str(getattr(response, "text", "") or "")
    found = _from_body(html)
    if not found and diagnostics is not None:
        diagnostics.append(describe_page(html, final))
    return found or url


def resolve_article_urls(
    articles: Iterable[Article], client, limit: int = 40
) -> Tuple[List[Article], int, int, List[str]]:
    """Resolve aggregator links in place, up to ``limit`` requests.

    Returns ``(articles, resolved, attempted, diagnostics)`` so a run can report
    how well resolution is working rather than silently handing back opaque
    links, and say what it saw when it could not.
    """
    items = list(articles)
    budget = limit
    resolved_count = 0
    attempted = 0
    # Two articles resolving to the same address means the parse latched onto
    # something shared by every page — a logo, a masthead link — rather than
    # the story. The first such hit is kept; the rest keep their own links.
    assigned: set[str] = set()
    diagnostics: List[str] = []
    consecutive_failures = 0

    for article in items:
        if budget <= 0:
            break
        if not needs_resolution(article.url):
            continue
        if consecutive_failures >= GIVE_UP_AFTER:
            continue
        budget -= 1
        attempted += 1
        notes: List[str] = []
        resolved = canonical_url(resolve_one(article.url, client, notes))
        if notes and len(diagnostics) < 3:
            diagnostics.append(notes[0])
        if resolved in assigned:
            LOG.warning("ignoring repeated resolution target %s", resolved[:80])
            resolved = article.url
        if (
            resolved
            and resolved != article.url
            and not needs_resolution(resolved)
            and looks_like_article(resolved)
        ):
            assigned.add(resolved)
            article.url = resolved
            article.source_domain = domain_of(resolved)
            article.article_id = ""
            article.__post_init__()
            resolved_count += 1
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures == GIVE_UP_AFTER:
                LOG.warning(
                    "%d consecutive resolution failures; skipping the rest of this run",
                    GIVE_UP_AFTER,
                )
                diagnostics.append(
                    f"gave up after {GIVE_UP_AFTER} consecutive failures; "
                    "remaining links left as aggregator URLs"
                )
        client.sleep()
    return items, resolved_count, attempted, diagnostics
