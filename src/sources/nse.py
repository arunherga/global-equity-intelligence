"""NSE corporate announcements.

NSE's API requires a session cookie obtained by first loading the site, and it
frequently refuses datacentre IP ranges outright — including GitHub Actions
runners. That is a documented outcome, not a bug to hide: the failure is
recorded in Run Diagnostics and the run continues on the other sources.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..models import Article, SourceType
from ..normalize import parse_date
from .base import CollectionContext, HttpClient, Source, SourceError


class NseSource(Source):
    name = "nse"

    def __init__(self, config: Dict[str, Any], client: HttpClient) -> None:
        super().__init__(config, client)
        self.base_url = str(config.get("base_url", "https://www.nseindia.com"))
        self.announcements_url = str(
            config.get("announcements_url", f"{self.base_url}/api/corporate-announcements")
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self.base_url}/companies-listing/corporate-filings-announcements",
            "X-Requested-With": "XMLHttpRequest",
        }

    def warm_session(self) -> bool:
        """NSE hands out the cookie its API insists on only via the website."""
        try:
            self.client.get(self.base_url, headers={"Accept": "text/html"})
            return True
        except SourceError as exc:
            self.record_error("session warm-up", exc)
            return False

    def fetch(self, context: CollectionContext) -> List[Article]:
        if not self.warm_session():
            return []

        articles: List[Article] = []
        for profile in context.profiles:
            if "NSE" not in {x.upper() for x in profile.exchange}:
                continue
            if self.should_give_up():
                break
            self.attempted += 1
            url = f"{self.announcements_url}?index=equities&symbol={profile.ticker}"
            try:
                response = self.client.get(url, headers=self._headers())
                payload = response.json()
            except SourceError as exc:
                self.record_error(f"{profile.ticker} announcements", exc)
                continue
            except ValueError as exc:
                self.record_error(f"{profile.ticker} announcements", f"non-JSON response ({exc})")
                continue

            rows = payload if isinstance(payload, list) else payload.get("data", [])
            if not isinstance(rows, list):
                self.record_note(profile.ticker, "unexpected payload shape")
                continue
            self.succeeded += 1
            self.note_success()

            for row in rows[: context.max_articles_per_query]:
                subject = (row.get("desc") or row.get("subject") or "").strip()
                detail = (row.get("attchmntText") or row.get("smIndustry") or "").strip()
                link = row.get("attchmntFile") or row.get("fileName") or self.base_url
                if not subject:
                    continue
                article = Article(
                    title=f"{profile.company}: {subject}",
                    url=link,
                    source_name="NSE corporate announcement",
                    source_domain="nseindia.com",
                    source_type=SourceType.COMPANY_EXCHANGE_FILING,
                    summary=detail[:800],
                    published=parse_date(row.get("an_dt") or row.get("exchdisstime")),
                    collector=self.name,
                    tickers_hint=[profile.ticker],
                    is_official=True,
                )
                articles.append(article)
            self.client.sleep()
        return articles
