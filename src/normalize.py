"""Article normalisation: URLs, source names, titles and dates.

Raw feed items are messy. Google News wraps every link in a redirect and
appends the publisher to the headline; exchange feeds use their own date
formats. Normalisation happens once, before any matching.
"""

from __future__ import annotations

import base64
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from .config import Config
from .models import Article, SourceType

_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "referrer",
    "sourceid", "cmpid", "ncid", "__source", "oc", "ito", "at_medium",
}

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

_ENTITIES = {
    "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'",
    "&apos;": "'", "&nbsp;": " ", "&#8217;": "'", "&#8216;": "'",
    "&#8220;": '"', "&#8221;": '"', "&rsquo;": "'", "&lsquo;": "'",
    "&ldquo;": '"', "&rdquo;": '"', "&ndash;": "-", "&mdash;": "-",
    "&hellip;": "...", "&#160;": " ", "&#8211;": "-", "&#8212;": "-",
}


def clean_text(value: Optional[str]) -> str:
    """Strip HTML, decode entities, collapse whitespace."""
    if not value:
        return ""
    text = _HTML_TAG.sub(" ", str(value))
    for entity, replacement in _ENTITIES.items():
        text = text.replace(entity, replacement)
    text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
    return _WS.sub(" ", text).strip()


def strip_publisher_suffix(title: str) -> tuple[str, str]:
    """Google News titles look like ``Headline - The Economic Times``."""
    text = clean_text(title)
    match = re.search(r"\s+[-–]\s+([^-–]{2,60})$", text)
    if match:
        publisher = match.group(1).strip()
        # Avoid chopping real headlines that merely end in a short clause.
        if len(publisher.split()) <= 7 and not publisher.endswith((".", "?", "!")):
            return text[: match.start()].strip(), publisher
    return text, ""


def domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc.split(":")[0]


def unwrap_redirect(url: str) -> str:
    """Resolve common redirect wrappers without making a network request."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    host = parsed.netloc.lower()
    query = parse_qs(parsed.query)

    # Google/Bing style ?url= / ?u= wrappers
    for key in ("url", "u", "q", "target"):
        if key in query:
            candidate = unquote(query[key][0])
            if candidate.startswith("http"):
                return candidate

    # news.google.com/rss/articles/<base64> — best effort, often opaque.
    if "news.google.com" in host and "/articles/" in parsed.path:
        token = parsed.path.rsplit("/", 1)[-1].split("?")[0]
        decoded = _try_decode_google_token(token)
        if decoded:
            return decoded
    return url


def _try_decode_google_token(token: str) -> Optional[str]:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii", "ignore"))
    except Exception:  # noqa: BLE001 - malformed tokens are expected
        return None
    match = re.search(rb"https?://[\w\-./?%&=:+#@~,;!$'()*\[\]]+", raw)
    if not match:
        return None
    candidate = match.group(0).decode("utf-8", "ignore")
    # Trailing binary noise sometimes leaks into the match.
    candidate = re.split(r"[\x00-\x1f]", candidate)[0]
    return candidate if len(candidate) > 12 else None


def canonical_url(url: str) -> str:
    """Drop tracking parameters and fragments so duplicates collapse."""
    if not url:
        return ""
    resolved = unwrap_redirect(url.strip())
    try:
        parsed = urlparse(resolved)
    except ValueError:
        return resolved
    if not parsed.scheme:
        return resolved
    query = parse_qs(parsed.query, keep_blank_values=False)
    kept = {k: v for k, v in query.items() if k.lower() not in _TRACKING_PARAMS}
    query_string = "&".join(f"{k}={v[0]}" for k, v in sorted(kept.items()))
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", query_string, ""))


def parse_date(value: Optional[str]) -> Optional[datetime]:
    """Parse the date formats seen across RSS, GDELT and exchange APIs."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    # RFC 822 (RSS)
    try:
        parsed = parsedate_to_datetime(text)
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass

    iso_candidate = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso_candidate)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass

    for fmt in (
        "%Y%m%dT%H%M%SZ",        # GDELT
        "%Y%m%d%H%M%S",
        "%d-%b-%Y %H:%M:%S",     # BSE
        "%d-%b-%Y",
        "%d %b %Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize_article(article: Article, config: Optional[Config] = None) -> Article:
    """Return a cleaned copy-in-place of an article."""
    title, publisher = strip_publisher_suffix(article.title)
    article.title = title or clean_text(article.title)
    article.summary = clean_text(article.summary)[:1200]
    article.url = canonical_url(article.url)
    article.source_domain = domain_of(article.url) or article.source_domain
    if not article.source_name:
        article.source_name = publisher or article.source_domain

    if config is not None and article.source_type == SourceType.UNKNOWN_NEWS_SITE:
        article.source_type = config.source_type_for_domain(article.source_domain)

    if article.source_type in {
        SourceType.COMPANY_EXCHANGE_FILING,
        SourceType.REGULATOR,
        SourceType.COMPANY_IR,
    }:
        article.is_official = True

    # Recompute the identity now that the fields are clean.
    article.article_id = ""
    article.__post_init__()
    return article


def normalize_all(
    articles: Iterable[Article], config: Optional[Config] = None
) -> List[Article]:
    out: List[Article] = []
    for article in articles:
        try:
            cleaned = normalize_article(article, config)
        except Exception:  # noqa: BLE001 - one bad item must not kill the run
            continue
        if cleaned.title and cleaned.url:
            out.append(cleaned)
    return out


def within_lookback(
    article: Article, hours: int, now: Optional[datetime] = None, allow_undated: bool = True
) -> bool:
    if article.published is None:
        return allow_undated
    reference = now or datetime.now(timezone.utc)
    return article.published >= reference - timedelta(hours=hours)
