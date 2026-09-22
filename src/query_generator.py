"""Automatic query generation from company profiles.

Hand-maintaining hundreds of search strings does not survive contact with a
ten-company watchlist. Queries are derived from the profile instead, so adding
a competitor or a commodity to ``watchlist.yaml`` immediately changes what the
agent searches for.

Queries are budgeted per company and allocated across *kinds*, because the
whole point of this system is that the international, commodity and policy
queries matter as much as the ones carrying the company's name.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional

from .models import ExposureType, Relationship
from .profiles.loader import CompanyProfile


class QueryKind(str, Enum):
    COMPANY = "COMPANY"
    PRODUCT = "PRODUCT"
    SECTOR = "SECTOR"
    COMMODITY = "COMMODITY"
    COMPETITOR = "COMPETITOR"
    CUSTOMER = "CUSTOMER"
    SUPPLIER = "SUPPLIER"
    REGULATORY = "REGULATORY"
    INTERNATIONAL = "INTERNATIONAL"
    MACRO = "MACRO"
    EXTRA = "EXTRA"


# How the per-company budget is divided. Ordering matters: earlier kinds are
# filled first when the budget is tight.
DEFAULT_QUOTA: Dict[QueryKind, int] = {
    QueryKind.COMPANY: 5,
    QueryKind.EXTRA: 6,
    QueryKind.SECTOR: 4,
    QueryKind.INTERNATIONAL: 4,
    QueryKind.COMMODITY: 3,
    QueryKind.REGULATORY: 2,
    QueryKind.COMPETITOR: 2,
    QueryKind.CUSTOMER: 1,
    QueryKind.SUPPLIER: 1,
    QueryKind.PRODUCT: 1,
    QueryKind.MACRO: 1,
}

KIND_RELATIONSHIP: Dict[QueryKind, Relationship] = {
    QueryKind.COMPANY: Relationship.DIRECT,
    QueryKind.PRODUCT: Relationship.SECTOR,
    QueryKind.SECTOR: Relationship.SECTOR,
    QueryKind.COMMODITY: Relationship.INDIRECT,
    QueryKind.COMPETITOR: Relationship.INDIRECT,
    QueryKind.CUSTOMER: Relationship.INDIRECT,
    QueryKind.SUPPLIER: Relationship.INDIRECT,
    QueryKind.REGULATORY: Relationship.INDIRECT,
    QueryKind.INTERNATIONAL: Relationship.INDIRECT,
    QueryKind.MACRO: Relationship.MACRO,
    QueryKind.EXTRA: Relationship.INDIRECT,
}

KIND_EXPOSURE: Dict[QueryKind, ExposureType] = {
    QueryKind.COMPANY: ExposureType.COMPANY,
    QueryKind.PRODUCT: ExposureType.PRODUCT,
    QueryKind.SECTOR: ExposureType.INDUSTRY,
    QueryKind.COMMODITY: ExposureType.COMMODITY,
    QueryKind.COMPETITOR: ExposureType.COMPETITOR,
    QueryKind.CUSTOMER: ExposureType.CUSTOMER,
    QueryKind.SUPPLIER: ExposureType.SUPPLIER,
    QueryKind.REGULATORY: ExposureType.REGULATION,
    QueryKind.INTERNATIONAL: ExposureType.MACRO,
    QueryKind.MACRO: ExposureType.MACRO,
    QueryKind.EXTRA: ExposureType.INDUSTRY,
}

# Action words appended to company queries to surface material developments
# rather than share-price chatter.
COMPANY_MODIFIERS = ("order", "results", "expansion", "contract")


@dataclass(frozen=True)
class Query:
    """One search string plus why it exists."""

    text: str
    ticker: str
    kind: QueryKind
    international: bool = False
    expected_relationship: Relationship = Relationship.INDIRECT
    exposure_type: ExposureType = ExposureType.INDUSTRY
    term: str = ""

    def key(self) -> str:
        return f"{self.ticker}|{self.text.lower()}"


def _quote(value: str) -> str:
    return f'"{value}"' if " " in value.strip() else value.strip()


def _take(values: Iterable[str], count: int) -> List[str]:
    out: List[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= count:
            break
    return out


def generate_for_profile(
    profile: CompanyProfile,
    budget: int = 26,
    quota: Optional[Dict[QueryKind, int]] = None,
) -> List[Query]:
    """Deterministic query set for one company."""
    quota = dict(quota or DEFAULT_QUOTA)
    buckets: Dict[QueryKind, List[Query]] = {kind: [] for kind in QueryKind}

    def add(kind: QueryKind, text: str, term: str = "", international: bool = False) -> None:
        text = " ".join(text.split())
        if not text:
            return
        query = Query(
            text=text,
            ticker=profile.ticker,
            kind=kind,
            international=international,
            expected_relationship=KIND_RELATIONSHIP[kind],
            exposure_type=KIND_EXPOSURE[kind],
            term=term or text,
        )
        if all(q.text.lower() != query.text.lower() for q in buckets[kind]):
            buckets[kind].append(query)

    # 1. The company itself -------------------------------------------------
    primary = [a.value for a in profile.primary_aliases] or [profile.company]
    add(QueryKind.COMPANY, _quote(primary[0]), primary[0])
    for alias in primary[1:3]:
        add(QueryKind.COMPANY, _quote(alias), alias)
    # Prefer the shortest alias that is still part of the registered name:
    # "Waaree Energies", not "Waaree Solar".
    inside = [a for a in primary if a.lower() in profile.company.lower()]
    short = min(inside or primary, key=len)
    for modifier in COMPANY_MODIFIERS:
        add(QueryKind.COMPANY, f"{_quote(short)} {modifier}", short)

    # 2. Author-curated extras come next: they encode judgement the
    #    generator cannot derive.
    for extra in profile.queries_extra:
        international = any(
            token in extra.lower()
            for token in ("us ", "china", "eu ", "europe", "global", "international", "fda")
        )
        add(QueryKind.EXTRA, extra, extra, international)

    # 3. Sector, product and technology -------------------------------------
    for topic in _take(profile.sector_topics, 8):
        add(QueryKind.SECTOR, topic, topic)
    for product in _take(profile.brands + profile.products, 4):
        add(QueryKind.PRODUCT, f"{product} {profile.country}", product)

    # 4. International exposure — the reason this system exists -------------
    for topic in _take(profile.international_topics, 8):
        add(QueryKind.INTERNATIONAL, topic, topic, international=True)
    for market in _take(profile.export_markets, 2):
        add(QueryKind.INTERNATIONAL, f"{market} {profile.industry or 'imports'} India", market, True)

    # 5. Commodities and currencies -----------------------------------------
    for commodity in _take([c.name for c in profile.commodities], 5):
        add(QueryKind.COMMODITY, f"{commodity} price", commodity)
    for currency in _take(profile.currencies, 1):
        add(QueryKind.MACRO, f"{currency} rupee", currency)
    for topic in _take(profile.macro_topics, 3):
        add(QueryKind.MACRO, topic, topic)

    # 6. Regulators and government ------------------------------------------
    for regulator in _take(profile.regulators, 3):
        if regulator.upper() in {"NSE", "BSE", "SEBI"}:
            continue
        add(QueryKind.REGULATORY, f"{regulator} {profile.industry or ''}".strip(), regulator)
    for dependency in _take(profile.government_dependencies, 2):
        add(QueryKind.REGULATORY, dependency, dependency)

    # 7. Ecosystem -----------------------------------------------------------
    for competitor in _take(profile.competitors, 4):
        add(QueryKind.COMPETITOR, _quote(competitor), competitor)
    for customer in _take(profile.customers, 2):
        add(QueryKind.CUSTOMER, f"{_quote(customer)} {profile.industry or ''}".strip(), customer)
    for supplier in _take(profile.suppliers, 2):
        add(QueryKind.SUPPLIER, _quote(supplier), supplier)

    return _allocate(buckets, quota, budget)


def _allocate(
    buckets: Dict[QueryKind, List[Query]], quota: Dict[QueryKind, int], budget: int
) -> List[Query]:
    """Fill the budget by quota, then spend anything left over in order."""
    chosen: List[Query] = []
    used: Dict[QueryKind, int] = {kind: 0 for kind in QueryKind}

    for kind, limit in quota.items():
        for query in buckets.get(kind, [])[:limit]:
            if len(chosen) >= budget:
                return chosen
            chosen.append(query)
            used[kind] += 1

    if len(chosen) < budget:
        for kind in quota:
            for query in buckets.get(kind, [])[used[kind] :]:
                if len(chosen) >= budget:
                    return chosen
                chosen.append(query)
                used[kind] += 1
    return chosen


def generate_queries(
    profiles: Iterable[CompanyProfile],
    budget: int = 26,
    quota: Optional[Dict[QueryKind, int]] = None,
) -> Dict[str, List[Query]]:
    """Queries for every company, keyed by ticker."""
    return {p.ticker: generate_for_profile(p, budget=budget, quota=quota) for p in profiles}


def flatten(queries: Dict[str, List[Query]]) -> List[Query]:
    """All queries, de-duplicated by text while keeping the first owner.

    Two companies often want the same search (``RBI monetary policy`` serves
    both banks). The query is issued once; matching decides afterwards which
    companies the results actually touch.
    """
    seen: Dict[str, Query] = {}
    for ticker in sorted(queries):
        for query in queries[ticker]:
            key = query.text.lower()
            if key not in seen:
                seen[key] = query
    return list(seen.values())
