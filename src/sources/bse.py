"""BSE corporate announcements.

BSE's JSON API is friendlier than NSE's but rate-limits, and it keys on a
numeric scrip code rather than the symbol. Codes are configured per company in
``watchlist.yaml`` (``ir.bse_code``); a company without one is skipped with a
note rather than guessed at.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

from ..models import Article, SourceType
from ..normalize import parse_date
from .base import CollectionContext, HttpClient, Source, SourceError


class BseSource(Source):
    name = "bse"

    def __init__(self, config: Dict[str, Any], client: HttpClient) -> None:
        super().__init__(config, client)
        self.base_url = str(config.get("base_url", "https://www.bseindia.com"))
        self.announcements_url = str(
            config.get(
                "announcements_url",
                "https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w",
            )
        )

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Referer": f"{self.base_url}/corporates/ann.html",
            "Origin": self.base_url,
        }

    @staticmethod
    def _name_matches(profile, row: Dict[str, Any]) -> bool:
        """Guard against a mis-configured scrip code."""
        from ..matching import contains_any, fold

        name = fold(str(row.get("SLONGNAME") or row.get("SCRIP_CD") or ""))
        if not name:
            return True  # nothing to check against; trust the code
        candidates = [a.value for a in profile.aliases] + [profile.company]
        first_words = [c.split()[0] for c in candidates if c.split()]
        return contains_any(name, candidates) or contains_any(name, first_words)

    def fetch(self, context: CollectionContext) -> List[Article]:
        articles: List[Article] = []
        end = date.today()
        start = end - timedelta(days=max(1, context.since_days))

        for profile in context.profiles:
            code = str(profile.ir.get("bse_code", "")).strip()
            if "BSE" not in {x.upper() for x in profile.exchange}:
                continue
            if not code:
                self.record_note(profile.ticker, "no ir.bse_code configured; skipped")
                continue

            if self.should_give_up():
                break
            self.attempted += 1
            url = (
                f"{self.announcements_url}?pageno=1&strCat=-1&strPrevDate="
                f"{start.strftime('%Y%m%d')}&strScrip={code}&strSearch=P"
                f"&strToDate={end.strftime('%Y%m%d')}&strType=C&subcategory=-1"
            )
            try:
                payload = self.client.get(url, headers=self._headers()).json()
            except SourceError as exc:
                self.record_error(f"{profile.ticker} announcements", exc)
                continue
            except ValueError as exc:
                self.record_error(f"{profile.ticker} announcements", f"non-JSON response ({exc})")
                continue

            rows = payload.get("Table", []) if isinstance(payload, dict) else []
            if isinstance(rows, list) and rows and not self._name_matches(profile, rows[0]):
                # A wrong scrip code silently returns another company's
                # filings, which is worse than no data at all.
                returned = (rows[0].get("SLONGNAME") or rows[0].get("NEWSSUB") or "?")[:60]
                self.record_error(
                    f"{profile.ticker} scrip code {code}",
                    f"returned filings for {returned!r}; discarded - verify ir.bse_code",
                )
                continue
            if not isinstance(rows, list):
                self.record_note(profile.ticker, "unexpected payload shape")
                continue
            self.succeeded += 1
            self.note_success()

            for row in rows[: context.max_articles_per_query]:
                headline = (row.get("HEADLINE") or row.get("NEWSSUB") or "").strip()
                if not headline:
                    continue
                attachment = row.get("ATTACHMENTNAME") or ""
                link = (
                    f"{self.base_url}/xml-data/corpfiling/AttachLive/{attachment}"
                    if attachment
                    else f"{self.base_url}/corporates/ann.html"
                )
                articles.append(
                    Article(
                        title=f"{profile.company}: {headline}",
                        url=link,
                        source_name="BSE corporate announcement",
                        source_domain="bseindia.com",
                        source_type=SourceType.COMPANY_EXCHANGE_FILING,
                        summary=(row.get("MORE") or "")[:800],
                        published=parse_date(row.get("News_submission_dt") or row.get("DT_TM")),
                        collector=self.name,
                        tickers_hint=[profile.ticker],
                        is_official=True,
                    )
                )
            self.client.sleep()
        return articles
