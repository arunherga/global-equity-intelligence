"""Government ministry pages.

Ministries publish infrequently and rarely offer a working feed, so these are
scraped as listing pages over a wider window. The same rule applies as
everywhere else: a blocked or restructured page is a recorded diagnostic, not
an exception that ends the run.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..matching import contains_any, fold
from ..models import Article, SourceType
from .base import CollectionContext, HttpClient, Source, SourceError, extract_links

# department key -> (display name, url, tickers it serves)
GOVERNMENT_SOURCES: List[Tuple[str, str, Tuple[str, ...]]] = [
    # Every URL below was opened and confirmed on 2026-09-23. The previous
    # set was guessed and five of six were dead: coal.nic.in has moved to
    # coal.gov.in, and the "/en/whats-new" path does not exist on any of
    # these sites. Ministry homepages are used rather than their listing
    # pages because they carry the same items with far better link text.
    ("Ministry of Coal", "https://coal.gov.in/", ("COALINDIA", "MSTCLTD")),
    ("Ministry of Power", "https://www.powermin.gov.in/", ("COALINDIA", "WAAREEENER")),
    ("Ministry of New and Renewable Energy", "https://mnre.gov.in/en/", ("WAAREEENER",)),
    ("Ministry of Steel", "https://steel.gov.in/", ("MSTCLTD",)),
    # Removed: the Department of Commerce site is a hash-route single-page
    # app (commerce.gov.in/#/documents/press-releases), so the server returns
    # the same shell HTML whatever the path and there is nothing to parse.
    # Removed: PIB's allRel.aspx defaults to Hindi and to whichever ministry
    # region it was last given; the ministry homepages above already link
    # their PIB releases with English titles.
]

# Anchor text that looks like a release rather than navigation.
RELEASE_HINTS = (
    "policy", "scheme", "auction", "tender", "notification", "release",
    "production", "capacity", "target", "guidelines", "approval", "import",
    "export", "duty", "incentive", "cabinet", "launch", "review", "mou",
)


class GovernmentSource(Source):
    name = "government"

    def fetch(self, context: CollectionContext) -> List[Article]:
        tickers = set(context.tickers)
        articles: List[Article] = []

        for label, url, serves in GOVERNMENT_SOURCES:
            if serves and tickers and not (set(serves) & tickers):
                continue
            if self.should_give_up():
                break
            self.attempted += 1
            try:
                response = self.client.get(url, headers={"Accept": "text/html,*/*"})
            except SourceError as exc:
                self.record_error(label, exc)
                continue

            found = self._from_page(label, response.text, url, list(serves))
            if found:
                self.succeeded += 1
                self.note_success()
            else:
                self.record_note(label, "page reachable but no release-like links found")
            articles.extend(found[: context.max_articles_per_query])
            self.client.sleep()
        return articles

    def _from_page(
        self, label: str, html: str, base_url: str, serves: List[str]
    ) -> List[Article]:
        articles: List[Article] = []
        seen: set[str] = set()
        for url, text in extract_links(html, base_url):
            cleaned = " ".join(text.split())
            folded = fold(cleaned)
            if len(cleaned) < 25 or folded in seen:
                continue
            if not contains_any(folded, RELEASE_HINTS):
                continue
            seen.add(folded)
            article = Article(
                title=f"{label}: {cleaned}",
                url=url,
                source_name=label,
                source_type=SourceType.REGULATOR,
                collector=self.name,
                is_official=True,
            )
            article.raw["serves_tickers"] = serves
            articles.append(article)
        return articles
