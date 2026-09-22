"""Automatic profile enrichment.

The watchlist is a starting configuration, not the whole truth about a
company. Enrichment reads the company's own material — website, investor
pages, annual report and investor-presentation links, exchange disclosures —
and extracts additional products, markets, subsidiaries and exposures.

Two rules make this safe to run unattended:

1. Enriched data is written to ``data/profiles/<TICKER>.enriched.json``, never
   back into ``watchlist.yaml``.
2. The loader merges enrichment *underneath* the manual configuration, so a
   manual value can never be overwritten or removed — only added to.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from ..config import Config
from ..matching import contains_any, fold
from ..sources.base import HttpClient, SourceError, extract_links
from .loader import CompanyProfile

LOG = logging.getLogger("gei.enrichment")

# Documents worth following from an investor page.
DOCUMENT_HINTS = (
    "annual report", "integrated report", "investor presentation",
    "earnings presentation", "corporate presentation", "prospectus",
    "red herring", "rhp", "offer document", "fact sheet", "investor update",
    "financial results", "shareholding pattern",
)

# Phrases that introduce facts worth extracting from a page of prose.
FACT_PATTERNS: Dict[str, Sequence[re.Pattern]] = {
    "products": (
        re.compile(r"(?:products?|portfolio) (?:includes?|comprises?|range)[:\s]+([^.]{10,200})", re.I),
        re.compile(r"manufactures?\s+([^.]{10,160})", re.I),
    ),
    "export_markets": (
        re.compile(r"export(?:s|ed|ing)?\s+to\s+([^.]{5,160})", re.I),
        re.compile(r"present in\s+([^.]{5,160})", re.I),
        re.compile(r"customers? (?:across|in)\s+([^.]{5,160})", re.I),
    ),
    "subsidiaries": (
        re.compile(r"(?:wholly[- ]owned subsidiar(?:y|ies))[:\s]+([^.]{5,160})", re.I),
        re.compile(r"subsidiar(?:y|ies)\s+(?:include|are|is)[:\s]+([^.]{5,160})", re.I),
    ),
    "customers": (
        re.compile(r"(?:key |major )?customers?\s+(?:include|are)[:\s]+([^.]{5,200})", re.I),
        re.compile(r"clients?\s+include[:\s]+([^.]{5,200})", re.I),
    ),
}

_SPLIT = re.compile(r"\s*(?:,|;|\band\b|\|)\s*", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

MAX_TERM_WORDS = 6
MIN_TERM_CHARS = 4


def _text_of(html: str) -> str:
    body = _TAG.sub(" ", html or "")
    return _WS.sub(" ", body)


def _split_terms(blob: str) -> List[str]:
    out: List[str] = []
    for chunk in _SPLIT.split(blob):
        term = chunk.strip(" .:-–—\t")
        if len(term) < MIN_TERM_CHARS or len(term.split()) > MAX_TERM_WORDS:
            continue
        if term.lower().startswith(("http", "www")):
            continue
        out.append(term)
    return out


class Enricher:
    """Fetches company material and extracts additional profile terms."""

    def __init__(self, config: Config, client: Optional[HttpClient] = None) -> None:
        self.config = config
        self.client = client or HttpClient.from_config(config.http)
        self.max_pages = int(config.get("enrichment.max_pages_per_company", 6))
        self.errors: List[str] = []
        self.notes: List[str] = []

    # -- storage ---------------------------------------------------------
    def path_for(self, ticker: str) -> Path:
        return self.config.storage_path("profiles_dir") / f"{ticker}.enriched.json"

    def is_stale(self, ticker: str) -> bool:
        path = self.path_for(ticker)
        if not path.exists():
            return True
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            fetched = datetime.fromisoformat(str(payload.get("fetched_at", "")))
        except (json.JSONDecodeError, OSError, ValueError):
            return True
        age_days = (datetime.now(timezone.utc) - fetched).days
        return age_days >= int(self.config.get("enrichment.refresh_days", 30))

    def save(self, profile: CompanyProfile, fields: Dict[str, List[str]], documents: List[Dict[str, str]]) -> Path:
        path = self.path_for(profile.ticker)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ticker": profile.ticker,
            "company": profile.company,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "source": "automatic enrichment (never overwrites watchlist.yaml)",
            "documents": documents,
            "fields": {k: v for k, v in fields.items() if v},
            "notes": list(self.notes),
            "errors": list(self.errors),
        }
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        tmp.replace(path)
        return path

    # -- fetching --------------------------------------------------------
    def enrich(self, profile: CompanyProfile) -> Dict[str, Any]:
        pages: List[tuple[str, str]] = []
        documents: List[Dict[str, str]] = []

        for url in self._seed_urls(profile):
            if len(pages) >= self.max_pages:
                break
            try:
                response = self.client.get(url, headers={"Accept": "text/html,*/*"})
            except SourceError as exc:
                self.errors.append(f"{url}: {exc}")
                continue
            html = response.text
            pages.append((url, html))
            documents.extend(self._documents(html, url))
            self.client.sleep()

        fields = self._extract(profile, pages)
        return {"fields": fields, "documents": documents[:20], "pages_read": len(pages)}

    def _seed_urls(self, profile: CompanyProfile) -> List[str]:
        urls: List[str] = []
        for key in ("investor_page", "website"):
            value = profile.ir.get(key)
            if value and value not in urls:
                urls.append(str(value))
        return urls

    def _documents(self, html: str, base_url: str) -> List[Dict[str, str]]:
        """Annual reports, presentations and prospectuses worth recording."""
        found: List[Dict[str, str]] = []
        for url, text in extract_links(html, base_url):
            label = " ".join(text.split())
            if not contains_any(fold(label), DOCUMENT_HINTS):
                continue
            found.append({"title": label[:160], "url": url})
        return found

    def _extract(
        self, profile: CompanyProfile, pages: Sequence[tuple[str, str]]
    ) -> Dict[str, List[str]]:
        known = self._known_terms(profile)
        collected: Dict[str, List[str]] = {}

        for _, html in pages:
            text = _text_of(html)
            for field_name, patterns in FACT_PATTERNS.items():
                for pattern in patterns:
                    for match in pattern.finditer(text):
                        for term in _split_terms(match.group(1)):
                            if term.lower() in known:
                                continue
                            bucket = collected.setdefault(field_name, [])
                            if term not in bucket and len(bucket) < 25:
                                bucket.append(term)
        if not collected:
            self.notes.append("no new terms extracted from company material")
        return collected

    @staticmethod
    def _known_terms(profile: CompanyProfile) -> Set[str]:
        known: Set[str] = set()
        for values in (
            [a.value for a in profile.aliases],
            profile.subsidiaries, profile.associates, profile.brands, profile.products,
            profile.services, profile.competitors, profile.suppliers, profile.customers,
            profile.countries, profile.export_markets,
        ):
            known |= {str(v).lower() for v in values}
        return known


def enrich_profiles(
    profiles: Iterable[CompanyProfile], config: Config, force: bool = False
) -> Dict[str, Path]:
    """Enrich every profile whose stored data is missing or stale."""
    if not config.get("enrichment.enabled", True):
        return {}
    enricher = Enricher(config)
    written: Dict[str, Path] = {}
    for profile in profiles:
        if not force and not enricher.is_stale(profile.ticker):
            LOG.info("%s: enrichment is current", profile.ticker)
            continue
        LOG.info("%s: enriching from company material", profile.ticker)
        enricher.errors = []
        enricher.notes = []
        result = enricher.enrich(profile)
        written[profile.ticker] = enricher.save(
            profile, result["fields"], result["documents"]
        )
    return written
