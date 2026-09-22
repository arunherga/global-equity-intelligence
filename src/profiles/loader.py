"""Watchlist + company-profile loading.

The manually maintained ``watchlist.yaml`` is authoritative. Enriched data
discovered automatically (annual reports, IR pages, exchange filings) lives in
``data/profiles/<TICKER>.enriched.json`` and is merged *underneath* the manual
configuration: enrichment can only ADD terms, never replace or delete them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

from ..config import DEFAULT_WATCHLIST_PATH, Config
from ..models import ExposureType

# Fields that enrichment may extend.
ENRICHABLE_FIELDS = (
    "aliases",
    "subsidiaries",
    "associates",
    "brands",
    "products",
    "services",
    "competitors",
    "suppliers",
    "customers",
    "countries",
    "export_markets",
    "currencies",
    "government_dependencies",
    "regulators",
    "sector_topics",
    "macro_topics",
    "international_topics",
    "technologies",
)


@dataclass(frozen=True)
class AliasSpec:
    """One way a company can be named in text."""

    value: str
    strength: str = "primary"   # primary | secondary
    requires: Tuple[str, ...] = ()
    excludes: Tuple[str, ...] = ()
    source: str = "manual"      # manual | enriched

    @property
    def is_primary(self) -> bool:
        return self.strength == "primary"

    @classmethod
    def parse(cls, entry: Any, source: str = "manual") -> Optional["AliasSpec"]:
        if isinstance(entry, str):
            text = entry.strip()
            return cls(value=text, source=source) if text else None
        if isinstance(entry, dict):
            value = str(entry.get("value", "")).strip()
            if not value:
                return None
            return cls(
                value=value,
                strength=str(entry.get("strength", "primary")).lower(),
                requires=tuple(str(x).lower() for x in entry.get("requires", [])),
                excludes=tuple(str(x).lower() for x in entry.get("excludes", [])),
                source=source,
            )
        return None


@dataclass(frozen=True)
class CommoditySpec:
    name: str
    role: str = "input"         # input | output | throughput
    source: str = "manual"

    @classmethod
    def parse(cls, entry: Any, source: str = "manual") -> Optional["CommoditySpec"]:
        if isinstance(entry, str):
            return cls(name=entry.strip(), source=source) if entry.strip() else None
        if isinstance(entry, dict):
            name = str(entry.get("name", "")).strip()
            if not name:
                return None
            role = str(entry.get("role", "input")).lower()
            if role not in {"input", "output", "throughput"}:
                role = "input"
            return cls(name=name, role=role, source=source)
        return None


@dataclass
class CompanyProfile:
    """Everything the pipeline knows about one watchlist company."""

    ticker: str
    company: str
    exchange: List[str] = field(default_factory=list)
    country: str = "India"
    market_segment: str = "MAIN"
    industry: str = ""
    ownership: str = ""

    aliases: List[AliasSpec] = field(default_factory=list)
    negative_aliases: List[str] = field(default_factory=list)

    subsidiaries: List[str] = field(default_factory=list)
    associates: List[str] = field(default_factory=list)
    brands: List[str] = field(default_factory=list)
    products: List[str] = field(default_factory=list)
    services: List[str] = field(default_factory=list)

    competitors: List[str] = field(default_factory=list)
    suppliers: List[str] = field(default_factory=list)
    customers: List[str] = field(default_factory=list)

    countries: List[str] = field(default_factory=list)
    export_markets: List[str] = field(default_factory=list)

    commodities: List[CommoditySpec] = field(default_factory=list)
    currencies: List[str] = field(default_factory=list)

    government_dependencies: List[str] = field(default_factory=list)
    regulators: List[str] = field(default_factory=list)

    sector_topics: List[str] = field(default_factory=list)
    macro_topics: List[str] = field(default_factory=list)
    international_topics: List[str] = field(default_factory=list)
    technologies: List[str] = field(default_factory=list)

    ir: Dict[str, Any] = field(default_factory=dict)
    queries_extra: List[str] = field(default_factory=list)

    enriched: Dict[str, Any] = field(default_factory=dict)
    enriched_terms: Dict[str, List[str]] = field(default_factory=dict)

    # -- derived ---------------------------------------------------------
    @property
    def primary_aliases(self) -> List[AliasSpec]:
        return [a for a in self.aliases if a.is_primary]

    @property
    def is_exporter(self) -> bool:
        return bool(self.export_markets)

    def is_international_topic(self, term: str) -> bool:
        lowered = term.lower()
        return any(t.lower() == lowered for t in self.international_topics)

    def commodity_role(self, name: str) -> str:
        lowered = name.lower()
        for spec in self.commodities:
            if spec.name.lower() == lowered:
                return spec.role
        return "input"

    def lexicon(self) -> Dict[ExposureType, List[str]]:
        """Exposure terms grouped by the relationship they imply."""
        return {
            ExposureType.SUBSIDIARY: list(self.subsidiaries) + list(self.associates),
            ExposureType.PRODUCT: list(self.brands) + list(self.products) + list(self.services),
            ExposureType.COMPETITOR: list(self.competitors),
            ExposureType.SUPPLIER: list(self.suppliers),
            ExposureType.CUSTOMER: list(self.customers),
            ExposureType.COMMODITY: [c.name for c in self.commodities],
            ExposureType.CURRENCY: list(self.currencies),
            ExposureType.REGULATION: list(self.regulators) + list(self.government_dependencies),
            ExposureType.INDUSTRY: list(self.sector_topics) + list(self.international_topics),
            ExposureType.GEOGRAPHY: list(self.countries) + list(self.export_markets),
            ExposureType.MACRO: list(self.macro_topics),
            ExposureType.TECHNOLOGY: list(self.technologies),
        }

    def summary_line(self) -> str:
        bits = [self.company, f"({self.ticker})"]
        if self.industry:
            bits.append(f"- {self.industry}")
        return " ".join(bits)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "company": self.company,
            "exchange": self.exchange,
            "country": self.country,
            "market_segment": self.market_segment,
            "industry": self.industry,
            "aliases": [a.value for a in self.aliases],
            "subsidiaries": self.subsidiaries,
            "associates": self.associates,
            "products": self.products,
            "competitors": self.competitors,
            "suppliers": self.suppliers,
            "customers": self.customers,
            "commodities": [{"name": c.name, "role": c.role} for c in self.commodities],
            "currencies": self.currencies,
            "regulators": self.regulators,
            "export_markets": self.export_markets,
            "sector_topics": self.sector_topics,
            "macro_topics": self.macro_topics,
            "international_topics": self.international_topics,
            "enriched_fields": sorted(self.enriched_terms),
        }


class WatchlistError(RuntimeError):
    pass


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        text = str(value).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


class Watchlist:
    """The 10 tracked companies, plus ticker normalisation."""

    def __init__(self, profiles: Dict[str, CompanyProfile], ticker_aliases: Dict[str, str]):
        self._profiles = profiles
        self._aliases = {k.upper(): v.upper() for k, v in ticker_aliases.items()}

    # -- container behaviour --------------------------------------------
    def __len__(self) -> int:
        return len(self._profiles)

    def __iter__(self):
        return iter(self._profiles.values())

    def __contains__(self, ticker: str) -> bool:
        return self.normalize(ticker) in self._profiles

    @property
    def tickers(self) -> List[str]:
        return list(self._profiles.keys())

    @property
    def profiles(self) -> List[CompanyProfile]:
        return list(self._profiles.values())

    def normalize(self, ticker: str) -> str:
        """Map user shorthand onto the canonical ticker (HDFC -> HDFCBANK)."""
        key = (ticker or "").strip().upper()
        if not key:
            return key
        if key in self._profiles:
            return key
        if key in self._aliases:
            return self._aliases[key]
        compact = key.replace(" ", "").replace(".", "").replace("-", "")
        if compact in self._profiles:
            return compact
        if compact in self._aliases:
            return self._aliases[compact]
        return key

    def get(self, ticker: str) -> CompanyProfile:
        key = self.normalize(ticker)
        if key not in self._profiles:
            raise KeyError(f"Unknown ticker: {ticker!r} (normalised to {key!r})")
        return self._profiles[key]

    def select(self, tickers: Optional[Iterable[str]] = None) -> List[CompanyProfile]:
        if not tickers:
            return self.profiles
        chosen: List[CompanyProfile] = []
        for raw in tickers:
            chosen.append(self.get(raw))
        # preserve watchlist order, drop duplicates
        wanted = {p.ticker for p in chosen}
        return [p for p in self.profiles if p.ticker in wanted]


def _build_profile(ticker: str, data: Dict[str, Any], defaults: Dict[str, Any]) -> CompanyProfile:
    aliases: List[AliasSpec] = []
    for entry in data.get("aliases", []) or []:
        spec = AliasSpec.parse(entry)
        if spec:
            aliases.append(spec)
    company = str(data.get("company", ticker))
    if not any(a.value.lower() == company.lower() for a in aliases):
        aliases.insert(0, AliasSpec(value=company, strength="primary"))

    commodities: List[CommoditySpec] = []
    for entry in data.get("commodities", []) or []:
        spec = CommoditySpec.parse(entry)
        if spec:
            commodities.append(spec)

    return CompanyProfile(
        ticker=ticker,
        company=company,
        exchange=[str(x) for x in data.get("exchange", defaults.get("exchange", []))],
        country=str(data.get("country", defaults.get("country", "India"))),
        market_segment=str(data.get("market_segment", "MAIN")),
        industry=str(data.get("industry", "")),
        ownership=str(data.get("ownership", "")),
        aliases=aliases,
        negative_aliases=_dedupe(data.get("negative_aliases", []) or []),
        subsidiaries=_dedupe(data.get("subsidiaries", []) or []),
        associates=_dedupe(data.get("associates", []) or []),
        brands=_dedupe(data.get("brands", []) or []),
        products=_dedupe(data.get("products", []) or []),
        services=_dedupe(data.get("services", []) or []),
        competitors=_dedupe(data.get("competitors", []) or []),
        suppliers=_dedupe(data.get("suppliers", []) or []),
        customers=_dedupe(data.get("customers", []) or []),
        countries=_dedupe(data.get("countries", []) or []),
        export_markets=_dedupe(data.get("export_markets", []) or []),
        commodities=commodities,
        currencies=_dedupe(data.get("currencies", []) or []),
        government_dependencies=_dedupe(data.get("government_dependencies", []) or []),
        regulators=_dedupe(data.get("regulators", []) or []),
        sector_topics=_dedupe(data.get("sector_topics", []) or []),
        macro_topics=_dedupe(data.get("macro_topics", []) or []),
        international_topics=_dedupe(data.get("international_topics", []) or []),
        technologies=_dedupe(data.get("technologies", []) or []),
        ir=dict(data.get("ir", {}) or {}),
        queries_extra=_dedupe(data.get("queries_extra", []) or []),
    )


def apply_enrichment(profile: CompanyProfile, enriched: Dict[str, Any]) -> CompanyProfile:
    """Merge enriched data *under* manual configuration.

    Manual values are never removed or reordered; enrichment may only append
    terms that are not already present.
    """
    if not enriched:
        return profile

    profile.enriched = dict(enriched)
    payload = enriched.get("fields", enriched)

    for field_name in ENRICHABLE_FIELDS:
        new_values = payload.get(field_name)
        if not new_values:
            continue
        current = getattr(profile, field_name)

        if field_name == "aliases":
            existing = {a.value.lower() for a in profile.aliases}
            added: List[str] = []
            for entry in new_values:
                spec = AliasSpec.parse(entry, source="enriched")
                if spec and spec.value.lower() not in existing:
                    profile.aliases.append(spec)
                    existing.add(spec.value.lower())
                    added.append(spec.value)
            if added:
                profile.enriched_terms.setdefault(field_name, []).extend(added)
            continue

        existing_lower = {str(v).lower() for v in current}
        added = []
        for value in new_values:
            text = str(value).strip()
            if text and text.lower() not in existing_lower:
                current.append(text)
                existing_lower.add(text.lower())
                added.append(text)
        if added:
            profile.enriched_terms.setdefault(field_name, []).extend(added)

    # Commodities carry a role, handled separately.
    for entry in payload.get("commodities", []) or []:
        spec = CommoditySpec.parse(entry, source="enriched")
        if spec and not any(c.name.lower() == spec.name.lower() for c in profile.commodities):
            profile.commodities.append(spec)
            profile.enriched_terms.setdefault("commodities", []).append(spec.name)

    # Free-form enrichment facts kept for the report / AI context only.
    return profile


def load_watchlist(
    path: Optional[Path] = None,
    config: Optional[Config] = None,
    use_enrichment: bool = True,
) -> Watchlist:
    watchlist_path = Path(path) if path else DEFAULT_WATCHLIST_PATH
    if not watchlist_path.exists():
        raise WatchlistError(f"watchlist not found: {watchlist_path}")

    with open(watchlist_path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    tickers_data = data.get("tickers") or {}
    if not tickers_data:
        raise WatchlistError("watchlist.yaml contains no tickers")

    defaults = data.get("defaults", {}) or {}
    profiles: Dict[str, CompanyProfile] = {}
    for ticker, payload in tickers_data.items():
        key = str(ticker).strip().upper()
        profiles[key] = _build_profile(key, payload or {}, defaults)

    if use_enrichment and config is not None:
        profiles_dir = config.storage_path("profiles_dir")
        for ticker, profile in profiles.items():
            enriched_file = profiles_dir / f"{ticker}.enriched.json"
            if enriched_file.exists():
                try:
                    with open(enriched_file, "r", encoding="utf-8") as handle:
                        apply_enrichment(profile, json.load(handle))
                except (json.JSONDecodeError, OSError):
                    # A corrupt enrichment file must never break a run.
                    continue

    ticker_aliases = {str(k): str(v) for k, v in (data.get("ticker_aliases") or {}).items()}
    return Watchlist(profiles, ticker_aliases)
