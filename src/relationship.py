"""Relationship detection: how strongly does an event touch a company?

Entity matching says *the article names the company*. Exposure matching says
*the article touches something the company depends on*. This module turns both
into one of six labels, and — importantly — records why.

    DIRECT           the company itself is the subject
    INDIRECT_STRONG  a named competitor, customer, supplier, group company,
                     input/output commodity or a regulation aimed at its market
    INDIRECT         a real but less immediate exposure
    SECTOR           its industry, not it
    MACRO            economy-wide conditions it shares with everyone
    WEAK             a passing or generic connection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from .classify import Classification
from .entity_match import EntityMatch
from .exposure_match import INTERNATIONAL_DETAIL, ExposureResult, is_named_entity
from .models import Article, EventCategory, ExposureMatch, ExposureType, Relationship
from .profiles.loader import CompanyProfile

# Exposure types that constitute *specific*, checkable evidence.
SPECIFIC_TYPES = {
    ExposureType.SUBSIDIARY,
    ExposureType.COMPETITOR,
    ExposureType.CUSTOMER,
    ExposureType.SUPPLIER,
    ExposureType.COMMODITY,
    ExposureType.REGULATION,
    ExposureType.PRODUCT,
    ExposureType.INDUSTRY,
    ExposureType.TECHNOLOGY,
}

BROAD_TYPES = {ExposureType.GEOGRAPHY, ExposureType.CURRENCY, ExposureType.MACRO}

# Categories that make an ecosystem mention materially relevant rather than
# incidental. "Competitor mentioned" is noise; "competitor wins a huge order"
# is not.
MATERIAL_CATEGORIES = {
    EventCategory.ORDER_WIN, EventCategory.ORDER_LOSS, EventCategory.EXPORT_ORDER,
    EventCategory.CONTRACT_WIN, EventCategory.CONTRACT_LOSS,
    EventCategory.CAPACITY_EXPANSION, EventCategory.NEW_PLANT, EventCategory.EXPANSION,
    EventCategory.EARNINGS, EventCategory.GUIDANCE, EventCategory.ACQUISITION,
    EventCategory.MERGER, EventCategory.SUPPLY_CHAIN, EventCategory.PRODUCT_FAILURE,
    EventCategory.CREDIT_RATING, EventCategory.FRAUD, EventCategory.CYBERSECURITY,
}

POLICY_CATEGORIES = {
    EventCategory.REGULATORY, EventCategory.GOVERNMENT_POLICY, EventCategory.TARIFF,
    EventCategory.TRADE_POLICY, EventCategory.EXPORT_RESTRICTION,
    EventCategory.IMPORT_RESTRICTION, EventCategory.TENDER,
}

PRICE_CATEGORIES = {
    EventCategory.COMMODITY, EventCategory.RAW_MATERIAL, EventCategory.SUPPLY_CHAIN,
    EventCategory.INDUSTRY_DEMAND,
}

MACRO_CATEGORIES = {
    EventCategory.MACRO, EventCategory.INTEREST_RATE, EventCategory.CURRENCY,
    EventCategory.GEOPOLITICAL,
}

# Headlines say "EU" and "US"; profiles say "European Union" and
# "United States". Normalise both sides before comparing.
GEO_ALIASES = {
    "eu": "europe", "european union": "europe", "us": "united states",
    "usa": "united states", "u s": "united states", "america": "united states",
    "uk": "united kingdom", "britain": "united kingdom", "prc": "china",
}


def _geo(value: str) -> str:
    lowered = value.strip().lower()
    return GEO_ALIASES.get(lowered, lowered)


_LADDER = [
    Relationship.WEAK,
    Relationship.MACRO,
    Relationship.SECTOR,
    Relationship.INDIRECT,
    Relationship.INDIRECT_STRONG,
    Relationship.DIRECT,
]


def _upgrade(current: Relationship, steps: int = 1) -> Relationship:
    index = min(_LADDER.index(current) + steps, len(_LADDER) - 1)
    return _LADDER[index]


def _at_least(current: Relationship, floor: Relationship) -> Relationship:
    return floor if floor.rank > current.rank else current


@dataclass
class StockLink:
    """One company's connection to one article, with the reasoning kept."""

    ticker: str
    relationship: Relationship
    entity: Optional[EntityMatch] = None
    exposures: List[ExposureMatch] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    evidence_weight: float = 0.0
    international: bool = False

    @property
    def is_direct(self) -> bool:
        return self.relationship == Relationship.DIRECT

    def exposure_types(self) -> List[ExposureType]:
        seen: List[ExposureType] = []
        for exposure in self.exposures:
            if exposure.exposure_type not in seen:
                seen.append(exposure.exposure_type)
        return seen

    def top_terms(self, limit: int = 4) -> List[str]:
        return [e.term for e in self.exposures[:limit]]


def determine_relationship(
    article: Article,
    profile: CompanyProfile,
    entity: Optional[EntityMatch],
    exposure: Optional[ExposureResult],
    classification: Classification,
) -> Optional[StockLink]:
    """Combine entity and exposure evidence into one labelled link."""
    exposures = list(exposure.matches) if exposure else []
    weight = exposure.total_weight if exposure else 0.0
    reasons: List[str] = []

    # ---- 1. The company itself ---------------------------------------
    if entity is not None:
        if entity.strength == "subsidiary":
            reasons.append(f"Subsidiary named: {entity.alias}")
        else:
            reasons.append(f"Company named: {entity.alias}")
        if entity.in_headline:
            reasons.append("Company appears in the headline")
        return StockLink(
            ticker=profile.ticker,
            relationship=Relationship.DIRECT,
            entity=entity,
            exposures=exposures,
            reasons=reasons,
            evidence_weight=weight + 4.0,
            international=_is_international(article, profile, exposures),
        )

    if not exposures:
        return None

    # ---- 2. Start from the strongest exposure hint --------------------
    relationship = Relationship.strongest([e.relationship for e in exposures])
    present = {e.exposure_type for e in exposures}
    specific = present & SPECIFIC_TYPES
    top = exposures[0]
    reasons.append(
        f"{top.exposure_type.value.replace('_', ' ').capitalize()} exposure: {top.term}"
    )

    # ---- 3. Upgrades, each with a stated reason -----------------------
    if ExposureType.SUBSIDIARY in present:
        relationship = _at_least(relationship, Relationship.INDIRECT_STRONG)
        reasons.append("Group company directly involved")

    named_counterparties = sorted({
        e.term for e in exposures
        if e.exposure_type in {
            ExposureType.COMPETITOR, ExposureType.CUSTOMER, ExposureType.SUPPLIER
        }
        and is_named_entity(e.term)
    })
    if named_counterparties and classification.has(*MATERIAL_CATEGORIES):
        relationship = _at_least(relationship, Relationship.INDIRECT_STRONG)
        reasons.append(
            "Material development at a known counterparty "
            f"({', '.join(named_counterparties[:3])})"
        )

    if ExposureType.COMMODITY in present and classification.has(*PRICE_CATEGORIES):
        commodity = next(e for e in exposures if e.exposure_type == ExposureType.COMMODITY)
        role = profile.commodity_role(commodity.term)
        if role in {"input", "output"}:
            relationship = _at_least(relationship, Relationship.INDIRECT_STRONG)
            reasons.append(f"Price move in a core {role} commodity ({commodity.term})")
        else:
            relationship = _at_least(relationship, Relationship.INDIRECT)
            reasons.append(f"Volume-relevant commodity ({commodity.term})")

    if ExposureType.REGULATION in present and classification.has(*POLICY_CATEGORIES):
        relationship = _at_least(relationship, Relationship.INDIRECT)
        regulator = next(e for e in exposures if e.exposure_type == ExposureType.REGULATION)
        reasons.append(f"Policy or regulatory action involving {regulator.term}")
        # A rule aimed at a market the company sells into is close to home.
        if _touches_export_market(profile, exposures):
            relationship = _at_least(relationship, Relationship.INDIRECT_STRONG)
            reasons.append("Rule applies to one of its export markets")

    international_topics = [
        e for e in exposures
        if e.exposure_type == ExposureType.INDUSTRY and INTERNATIONAL_DETAIL in e.detail
    ]
    if international_topics and classification.has(*(POLICY_CATEGORIES | PRICE_CATEGORIES)):
        relationship = _at_least(relationship, Relationship.INDIRECT_STRONG)
        reasons.append(
            "Foreign policy or price development in a monitored exposure topic "
            f"({international_topics[0].term})"
        )

    if ExposureType.INDUSTRY in present and classification.has(*POLICY_CATEGORIES):
        if _touches_export_market(profile, exposures):
            relationship = _at_least(relationship, Relationship.INDIRECT_STRONG)
            reasons.append("Sector policy in an export market")
        else:
            relationship = _at_least(relationship, Relationship.SECTOR)

    # Several independent specific exposures is itself evidence — but without
    # the company being named, nothing may reach DIRECT.
    if len(specific) >= 3 and weight >= 5.0:
        upgraded = _upgrade(relationship)
        if upgraded.rank > Relationship.INDIRECT_STRONG.rank:
            upgraded = Relationship.INDIRECT_STRONG
        if upgraded != relationship:
            relationship = upgraded
            reasons.append(
                f"{len(specific)} independent exposure types matched (weight {weight:.1f})"
            )

    if relationship == Relationship.DIRECT:
        # Belt and braces: DIRECT is reserved for a named company.
        relationship = Relationship.INDIRECT_STRONG

    # ---- 4. Caps, so broad news cannot masquerade as company news ------
    if not specific:
        if present <= {ExposureType.GEOGRAPHY}:
            relationship = Relationship.WEAK
            reasons.append("Only a geographic mention — no business exposure")
        elif present <= BROAD_TYPES:
            relationship = min(relationship, Relationship.MACRO, key=lambda r: r.rank)
            if classification.has(*MACRO_CATEGORIES):
                reasons.append("Economy-wide development")
            else:
                relationship = Relationship.WEAK
                reasons.append("Broad exposure without a macro event")

    if weight < 1.0:
        relationship = Relationship.WEAK
        reasons.append("Thin evidence")

    return StockLink(
        ticker=profile.ticker,
        relationship=relationship,
        entity=None,
        exposures=exposures,
        reasons=reasons,
        evidence_weight=weight,
        international=_is_international(article, profile, exposures),
    )


def _touches_export_market(profile: CompanyProfile, exposures: Sequence[ExposureMatch]) -> bool:
    markets = {_geo(m) for m in profile.export_markets}
    if not markets:
        return False
    return any(
        e.exposure_type == ExposureType.GEOGRAPHY and _geo(e.term) in markets
        for e in exposures
    )


def _is_international(
    article: Article, profile: CompanyProfile, exposures: Sequence[ExposureMatch]
) -> bool:
    """Is this foreign news? Used for the Global Events section."""
    foreign = {
        "united states", "usa", "us", "china", "europe", "european union", "eu",
        "japan", "germany", "spain", "russia", "australia", "indonesia", "brazil",
        "vietnam", "turkey", "middle east", "africa", "latin america", "united kingdom",
        "southeast asia", "korea", "taiwan",
    }
    for exposure in exposures:
        if exposure.exposure_type == ExposureType.GEOGRAPHY and exposure.term.lower() in foreign:
            return True
    if article.raw.get("query_kind") == "INTERNATIONAL":
        return True
    domain = (article.source_domain or "").lower()
    return domain.endswith((".com.au", ".co.uk", ".cn", ".eu")) or domain in {
        "reuters.com", "bloomberg.com", "ft.com", "wsj.com", "pv-magazine.com",
        "fda.gov", "ema.europa.eu", "iea.org", "irena.org", "trade.gov",
    }
