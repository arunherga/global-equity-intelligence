"""Reddit, for what customers actually say.

Marketplace reviews - Amazon, Flipkart, Nykaa, Myntra - are the obvious
place to look and are not available: their terms prohibit automated
collection and they block datacentre addresses, the same reason NSE and BSE
already fail from a hosted runner. Building a scraper for them would produce
something that breaks quietly and is trusted anyway.

Reddit's public JSON endpoints are the legitimate substitute, and often the
more candid one: people complain about a shampoo in r/IndianSkincareAddicts
in terms no review form invites. Unauthenticated access is allowed with a
descriptive User-Agent and modest rates, so this stays small on purpose - a
handful of searches per run, spaced out.

What comes back is opinion, not news. Items are marked SOCIAL_MEDIA, which
config.yaml scores at the bottom, and carry ``raw["consumer"] = True`` so the
sentiment layer can find them without them ever being mistaken for a filing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote_plus

from ..models import Article, SourceType
from .base import CollectionContext, HttpClient, Source, SourceError

# Reddit asks for a User-Agent that identifies the software and a contact
# point. A generic one is what gets a project rate-limited or blocked.
USER_AGENT = (
    "python:global-equity-intelligence:1.0 "
    "(personal watchlist research; contact via the GitHub repository)"
)


class RedditSource(Source):
    name = "reddit"

    def __init__(self, config: Dict[str, Any], client: HttpClient) -> None:
        super().__init__(config, client)
        self.base_url = str(config.get("base_url", "https://www.reddit.com"))
        self.subreddits: List[str] = [
            str(s) for s in (config.get("subreddits") or []) if str(s).strip()
        ]
        self.window = str(config.get("window", "week"))
        self.limit = int(config.get("limit_per_query", 25))
        self.max_queries = int(config.get("max_queries_per_run", 12))
        self.min_score = int(config.get("min_score", 1))

    # -- url building ----------------------------------------------------
    def build_url(self, term: str, subreddit: str = "") -> str:
        """Search one subreddit, or the whole site when none is given."""
        query = quote_plus(f'"{term}"' if " " in term else term)
        window = quote_plus(self.window)
        if subreddit:
            return (
                f"{self.base_url}/r/{subreddit}/search.json"
                f"?q={query}&restrict_sr=1&sort=new&t={window}&limit={self.limit}"
            )
        return (
            f"{self.base_url}/search.json"
            f"?q={query}&sort=new&t={window}&limit={self.limit}"
        )

    # -- what to look for ------------------------------------------------
    def terms_for(self, context: CollectionContext):
        """(ticker, term) pairs for companies that opted into consumer watch.

        Driven by an explicit ``consumer_terms`` list rather than by brands,
        because a term that works in a news index can be hopeless in a forum:
        "Freshara" finds the company, "gherkins" finds a thousand recipes.
        """
        for profile in context.profiles or []:
            for term in getattr(profile, "consumer_terms", []) or []:
                yield profile.ticker, str(term)

    def fetch(self, context: CollectionContext) -> List[Article]:
        articles: List[Article] = []
        spent = 0

        for ticker, term in self.terms_for(context):
            for subreddit in (self.subreddits or [""]):
                if spent >= self.max_queries or self.should_give_up():
                    break
                url = self.build_url(term, subreddit)
                self.attempted += 1
                spent += 1
                if context.dry_run and self.config.get("skip_network_on_dry_run"):
                    continue
                try:
                    response = self.client.get(url, headers={"User-Agent": USER_AGENT})
                except SourceError as exc:
                    self.record_error(f"{term!r} in r/{subreddit or 'all'}", exc)
                    continue
                self.succeeded += 1
                self.note_success()
                try:
                    payload = response.json()
                except ValueError as exc:
                    # Reddit serves an HTML block page rather than JSON when it
                    # is unhappy; saying so beats "0 items".
                    self.record_error(f"{term!r}: response was not JSON", exc)
                    continue
                articles.extend(
                    self.parse(payload, ticker=ticker, term=term, subreddit=subreddit)
                )
                self.client.sleep()

        if spent >= self.max_queries:
            self.record_note(
                "query budget",
                f"stopped at {self.max_queries} searches; raise "
                "sources.reddit.max_queries_per_run to widen",
            )
        return articles

    # -- parsing ---------------------------------------------------------
    def parse(
        self, payload: Dict[str, Any], ticker: str, term: str, subreddit: str = ""
    ) -> List[Article]:
        children = ((payload or {}).get("data") or {}).get("children") or []
        articles: List[Article] = []
        for child in children:
            post = (child or {}).get("data") or {}
            title = str(post.get("title") or "").strip()
            if not title:
                continue
            if int(post.get("score") or 0) < self.min_score:
                # A post nobody engaged with is one person, not a signal.
                continue
            if post.get("over_18") or post.get("removed_by_category"):
                continue
            permalink = str(post.get("permalink") or "")
            url = f"https://www.reddit.com{permalink}" if permalink else str(
                post.get("url") or ""
            )
            if not url:
                continue
            where = str(post.get("subreddit") or subreddit or "reddit")
            article = Article(
                title=title,
                url=url,
                source_name=f"r/{where}",
                source_domain="reddit.com",
                source_type=SourceType.SOCIAL_MEDIA,
                summary=str(post.get("selftext") or "")[:600],
                published=_epoch_to_datetime(post.get("created_utc")),
                collector=self.name,
                query=term,
                tickers_hint=[ticker],
            )
            article.raw.update({
                "consumer": True,
                "subreddit": where,
                "score": int(post.get("score") or 0),
                "num_comments": int(post.get("num_comments") or 0),
                "query_ticker": ticker,
                "query_term": term,
            })
            articles.append(article)
        return articles


def _epoch_to_datetime(value: Any) -> Optional[datetime]:
    """Reddit timestamps are epoch seconds, as a float, sometimes absent."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None
