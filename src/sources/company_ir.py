"""Company investor-relations pages.

The original source is always preferred: a company's own filing beats the
third re-write of it. But IR sites are inconsistent and their URLs rot, which
the live run of 2026-09-22 demonstrated - seven of the ten configured
investor pages were wrong, and most of them failed *silently*:

    /Investors.aspx        -> MSTC's soft 404, served as a normal page
    /investor-relations    -> Ksolves' "Page not found", 272 nav links on it
    /investors/            -> Waaree redirected to its homepage

Each of those was "reachable but nothing parseable" in the diagnostics, which
reads like a parser bug and was actually a dead link. So this collector now:

1. detects a soft 404 and says so, rather than reporting an empty success;
2. discovers the investor page from the site root when the configured one
   fails, instead of relying on a URL someone typed once;
3. follows one level down into announcement sub-pages; and
4. recovers a title from the document URL when the anchor text is "Download
   Now", which on several of these sites is the only text there is.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from urllib.parse import urlsplit

from ..matching import contains_any, fold
from ..models import Article, SourceType
from .base import (
    CollectionContext,
    HttpClient,
    Source,
    SourceError,
    extract_links,
    find_feed_links,
    parse_feed_entries,
)
from .ir_titles import best_title, is_generic_anchor, recognised_word_count, title_from_url

# A page that says this is not a page, whatever status code it returned.
SOFT_404_MARKERS = (
    "404", "page not found", "not found", "page you requested",
    "page cannot be found", "page doesn't exist", "page does not exist",
    "sorry, this page", "no longer available", "aspxerrorpath",
)

# Links that lead to the investor section or to filings within it.
IR_SECTION_HINTS = (
    "investor relations", "investor", "investors", "shareholder information",
    "stock exchange disclosure", "stock exchange intimation",
    "corporate announcement", "corporate announcements", "announcements",
    "regulation 46", "regulation 30", "disclosures", "financial results",
    "company announcements",
)

# Anchor or URL text that marks an actual filing.
FILING_HINTS = (
    "announcement", "intimation", "disclosure", "outcome", "board meeting",
    "press release", "results", "order", "allotment", "dividend", "transcript",
    "presentation", "regulation", "notice", "filing", "shareholding",
    "annual report", "credit rating", "investor meet", "analyst",
)

SKIP_HINTS = (
    "privacy", "cookie", "terms", "careers", "contact us", "sitemap",
    "login", "sign in", "subscribe", "unsubscribe", "home", "about us",
    "accessibility", "feedback", "screen reader", "skip to",
)

MAX_SUBPAGES = 2


def looks_like_soft_404(html: str, final_url: str = "") -> bool:
    """Many sites answer a dead URL with a normal-looking 200 page."""
    head = fold(html[:4000])
    if "aspxerrorpath" in fold(final_url):
        return True
    # <title> is the most reliable signal; the body is full of navigation.
    start = head.find("<title")
    if start != -1:
        end = head.find("</title", start)
        title = head[start:end] if end != -1 else head[start : start + 200]
        if contains_any(title, ("404", "page not found", "not found")):
            return True
    return contains_any(head[:1500], SOFT_404_MARKERS[:6])


class CompanyIrSource(Source):
    name = "company_ir"

    def __init__(self, config: Dict[str, Any], client: HttpClient) -> None:
        super().__init__(config, client)
        self.max_links = int(config.get("max_links_per_company", 12))
        self.max_subpages = int(config.get("max_subpages_per_company", MAX_SUBPAGES))

    # -- collection ------------------------------------------------------
    def fetch(self, context: CollectionContext) -> List[Article]:
        articles: List[Article] = []
        for profile in context.profiles:
            if self.should_give_up():
                break
            found = self._for_profile(profile)
            if found:
                self.succeeded += 1
                self.note_success()
            articles.extend(found[: self.max_links])
        return articles

    def _for_profile(self, profile) -> List[Article]:
        configured = profile.ir.get("investor_page") or profile.ir.get("website")
        website = profile.ir.get("website")
        if not configured:
            self.record_note(profile.ticker, "no IR page configured")
            return []

        self.attempted += 1
        page = self._get_page(profile, configured)

        # A configured investor page that is dead should not cost us the
        # company: fall back to the site root and find it ourselves.
        if page is None and website and website != configured:
            self.record_note(profile.ticker, f"falling back to {website}")
            page = self._get_page(profile, website)
        if page is None:
            return []

        html, url = page
        articles = self._from_feed(profile, html, url) or self._from_links(profile, html, url)

        for sub_url in self._discover_sections(html, url)[: self.max_subpages]:
            sub = self._get_page(profile, sub_url, quiet=True)
            if sub is None:
                continue
            articles.extend(self._from_links(profile, sub[0], sub[1]))

        if not articles:
            self.record_note(profile.ticker, f"no filing links found on {url}")
        return _dedupe_articles(articles)

    def _get_page(
        self, profile, url: str, quiet: bool = False
    ) -> Optional[Tuple[str, str]]:
        try:
            response = self.client.get(url, headers={"Accept": "text/html,*/*"})
        except SourceError as exc:
            if not quiet:
                self.record_error(f"{profile.ticker} IR page", exc)
            return None
        final_url = str(getattr(response, "url", "") or url)
        html = response.text
        if looks_like_soft_404(html, final_url):
            if not quiet:
                self.record_error(
                    f"{profile.ticker} IR page",
                    f"{url} answered with a not-found page (served as {final_url})",
                )
            return None
        self.client.sleep()
        return html, final_url

    # -- discovery -------------------------------------------------------
    def _discover_sections(self, html: str, base_url: str) -> List[str]:
        """Links on this page that lead deeper into the investor section."""
        host = urlsplit(base_url).netloc.lower()
        found: List[str] = []
        for url, text in extract_links(html, base_url):
            if urlsplit(url).netloc.lower() != host:
                continue
            haystack = fold(f"{text} {url}")
            if not contains_any(haystack, IR_SECTION_HINTS):
                continue
            if contains_any(haystack, SKIP_HINTS):
                continue
            if url.lower().endswith(".pdf") or url in found or url == base_url:
                continue
            found.append(url)
        return found

    # -- extraction ------------------------------------------------------
    def _from_feed(self, profile, html: str, page_url: str) -> List[Article]:
        for feed_url in find_feed_links(html, page_url)[:1]:
            try:
                content = self.client.get(feed_url).content
            except SourceError:
                continue
            items = list(
                parse_feed_entries(
                    content,
                    feed_name=f"{profile.company} IR",
                    collector=self.name,
                    source_type=SourceType.COMPANY_IR,
                    tickers_hint=[profile.ticker],
                )
            )
            for item in items:
                item.is_official = True
            if items:
                self.record_note(profile.ticker, f"using IR feed {feed_url}")
                return items
        return []

    def _from_links(self, profile, html: str, page_url: str) -> List[Article]:
        host = urlsplit(page_url).netloc.lower()
        articles: List[Article] = []
        seen: Set[str] = set()

        for url, anchor in extract_links(html, page_url):
            if url in seen:
                continue
            haystack = fold(f"{anchor} {url}")
            if contains_any(haystack, SKIP_HINTS):
                continue

            is_document = url.lower().split("?")[0].endswith(
                (".pdf", ".doc", ".docx", ".xls", ".xlsx")
            )
            same_site = urlsplit(url).netloc.lower() == host

            title = best_title(anchor, url)
            if not title or len(title) < 8:
                continue

            # Accept a link when it is either self-describing or a document on
            # this company's own site whose name reads like a filing.
            descriptive_anchor = not is_generic_anchor(anchor) and contains_any(
                fold(anchor), FILING_HINTS
            )
            document_with_meaning = (
                is_document and same_site and recognised_word_count(title) >= 1
            )
            url_looks_like_filing = same_site and contains_any(fold(url), FILING_HINTS)

            if not (descriptive_anchor or document_with_meaning or url_looks_like_filing):
                continue

            seen.add(url)
            article = Article(
                title=f"{profile.company}: {title}",
                url=url,
                source_name=f"{profile.company} investor relations",
                source_type=SourceType.COMPANY_IR,
                collector=self.name,
                tickers_hint=[profile.ticker],
                is_official=True,
            )
            article.raw["ir_page"] = page_url
            article.raw["title_source"] = "anchor" if descriptive_anchor else "filename"
            articles.append(article)
        return articles


def _dedupe_articles(articles: Sequence[Article]) -> List[Article]:
    seen: Set[str] = set()
    out: List[Article] = []
    for article in articles:
        if article.url in seen:
            continue
        seen.add(article.url)
        out.append(article)
    return out
