"""Explainable impact scoring.

Importance is not direction. A company announcing a huge new factory is a
high-impact event whose direction is genuinely uncertain — growth, but also
capital intensity and execution risk. So this module produces only
``impact_score`` (0-15) and ``confidence``; :mod:`src.direction` decides sign.

Every point that is added or subtracted carries a human-readable reason.
``impact_score`` and ``score_reasons`` are produced together and can never
drift apart.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .classify import Classification
from .exposure_match import is_named_entity
from .matching import contains_any, fold
from .models import (
    IMPACT_SCORE_MAX,
    IMPACT_SCORE_MIN,
    BusinessImpact,
    Event,
    EventCategory,
    ExposureType,
    Relationship,
    SourceType,
    TimeHorizon,
)
from .profiles.loader import CompanyProfile
from .relationship import StockLink

# --------------------------------------------------------------------------
# Category groupings used by the scoring signals
# --------------------------------------------------------------------------

EARNINGS_CATEGORIES = {EventCategory.EARNINGS, EventCategory.GUIDANCE}

ORDER_CATEGORIES = {
    EventCategory.ORDER_WIN, EventCategory.ORDER_LOSS, EventCategory.EXPORT_ORDER,
    EventCategory.CONTRACT_WIN, EventCategory.CONTRACT_LOSS,
}

REGULATORY_CATEGORIES = {
    EventCategory.REGULATORY, EventCategory.LEGAL, EventCategory.LITIGATION,
}

DEAL_CATEGORIES = {
    EventCategory.ACQUISITION, EventCategory.MERGER, EventCategory.DIVESTITURE,
}

CAPACITY_CATEGORIES = {
    EventCategory.CAPACITY_EXPANSION, EventCategory.NEW_PLANT, EventCategory.CAPEX,
}

PEOPLE_CATEGORIES = {
    EventCategory.MANAGEMENT_CHANGE, EventCategory.PROMOTER_ACTIVITY,
    EventCategory.INSIDER_BUYING, EventCategory.INSIDER_SELLING,
}

CUSTOMER_CATEGORIES = {EventCategory.NEW_CUSTOMER, EventCategory.CUSTOMER_LOSS}

SUPPLY_CATEGORIES = {EventCategory.SUPPLY_CHAIN, EventCategory.RAW_MATERIAL}

TRADE_CATEGORIES = {
    EventCategory.TARIFF, EventCategory.TRADE_POLICY,
    EventCategory.EXPORT_RESTRICTION, EventCategory.IMPORT_RESTRICTION,
}

POLICY_CATEGORIES = {EventCategory.GOVERNMENT_POLICY, EventCategory.TENDER}

COMMODITY_CATEGORIES = {EventCategory.COMMODITY}

INDUSTRY_CATEGORIES = {EventCategory.INDUSTRY_DEMAND, EventCategory.TECHNOLOGY}

# What counts as "something happened" at a customer or supplier. A dividend at
# a customer is not news for its vendors; a capacity expansion is.
MATERIAL_CATEGORIES_FOR_CHAIN = {
    EventCategory.CAPACITY_EXPANSION, EventCategory.NEW_PLANT, EventCategory.EXPANSION,
    EventCategory.GUIDANCE, EventCategory.PRODUCT_FAILURE, EventCategory.FRAUD,
    EventCategory.CYBERSECURITY, EventCategory.CREDIT_RATING, EventCategory.ACQUISITION,
    EventCategory.MERGER, EventCategory.INDUSTRY_DEMAND, EventCategory.TECHNOLOGY,
    EventCategory.REGULATORY, EventCategory.SUPPLY_CHAIN, EventCategory.INVESTMENT,
    EventCategory.CAPEX, EventCategory.PARTNERSHIP,
}

CRISIS_CATEGORIES = {
    EventCategory.FRAUD, EventCategory.CYBERSECURITY, EventCategory.PRODUCT_FAILURE,
}

# Language that signals a development is large rather than routine.
MAGNITUDE_UNITS = (
    "crore", "billion", "bn", "million", "mn", "lakh crore", "gw", "gigawatt",
    "mw", "megawatt", "mtpa", "tonnes", "tpa", "kt",
)
MAGNITUDE_WORDS = (
    "record", "largest", "biggest", "historic", "unprecedented", "sharp",
    "sharply", "collapse", "surge", "plunge", "crash", "multi-year", "all-time",
)

# Language that separates a surprise from a scheduled disclosure. Quarterly
# results are always news; they are only *critical* news when they surprise.
SURPRISE_WORDS = (
    "beats", "beat", "misses", "miss", "shock", "surprise", "record",
    "highest", "lowest", "doubles", "halves", "multi-fold", "jumps",
    "slumps", "surges", "plunges", "collapses", "warns", "profit warning",
    "sharply", "unexpected", "first time",
)

# Categories that are announced on a calendar rather than because something
# happened.
SCHEDULED_CATEGORIES = {
    EventCategory.EARNINGS,
    EventCategory.DIVIDEND,
    EventCategory.ANALYST_ACTION,
}

_NUMBER = re.compile(r"\d")
_PERCENT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|per cent|percent)")

# Impact dimensions implied by each category.
CATEGORY_IMPACTS: Dict[EventCategory, Tuple[BusinessImpact, ...]] = {
    EventCategory.EARNINGS: (BusinessImpact.REVENUE, BusinessImpact.MARGIN),
    EventCategory.GUIDANCE: (BusinessImpact.REVENUE, BusinessImpact.DEMAND),
    EventCategory.ORDER_WIN: (BusinessImpact.REVENUE, BusinessImpact.CAPACITY),
    EventCategory.ORDER_LOSS: (BusinessImpact.REVENUE, BusinessImpact.CAPACITY),
    EventCategory.EXPORT_ORDER: (BusinessImpact.EXPORTS, BusinessImpact.REVENUE),
    EventCategory.CONTRACT_WIN: (BusinessImpact.REVENUE,),
    EventCategory.CONTRACT_LOSS: (BusinessImpact.REVENUE,),
    EventCategory.NEW_CUSTOMER: (BusinessImpact.CUSTOMERS, BusinessImpact.REVENUE),
    EventCategory.CUSTOMER_LOSS: (BusinessImpact.CUSTOMERS, BusinessImpact.REVENUE),
    EventCategory.CAPACITY_EXPANSION: (BusinessImpact.CAPACITY, BusinessImpact.BALANCE_SHEET),
    EventCategory.NEW_PLANT: (BusinessImpact.CAPACITY, BusinessImpact.BALANCE_SHEET),
    EventCategory.EXPANSION: (BusinessImpact.GEOGRAPHY, BusinessImpact.REVENUE),
    EventCategory.CAPEX: (BusinessImpact.CASH_FLOW, BusinessImpact.BALANCE_SHEET),
    EventCategory.ACQUISITION: (BusinessImpact.BALANCE_SHEET, BusinessImpact.REVENUE),
    EventCategory.MERGER: (BusinessImpact.BALANCE_SHEET,),
    EventCategory.DIVESTITURE: (BusinessImpact.BALANCE_SHEET, BusinessImpact.CASH_FLOW),
    EventCategory.INVESTMENT: (BusinessImpact.BALANCE_SHEET,),
    EventCategory.PARTNERSHIP: (BusinessImpact.PRODUCT, BusinessImpact.CUSTOMERS),
    EventCategory.NEW_PRODUCT: (BusinessImpact.PRODUCT, BusinessImpact.REVENUE),
    EventCategory.PRODUCT_FAILURE: (BusinessImpact.REPUTATION, BusinessImpact.PRODUCT),
    EventCategory.EXPORT_RESTRICTION: (BusinessImpact.EXPORTS, BusinessImpact.REGULATION),
    EventCategory.IMPORT_RESTRICTION: (BusinessImpact.IMPORTS, BusinessImpact.SUPPLY),
    EventCategory.TARIFF: (BusinessImpact.EXPORTS, BusinessImpact.PRICING),
    EventCategory.TRADE_POLICY: (BusinessImpact.EXPORTS, BusinessImpact.REGULATION),
    EventCategory.REGULATORY: (BusinessImpact.REGULATION,),
    EventCategory.LEGAL: (BusinessImpact.REGULATION, BusinessImpact.REPUTATION),
    EventCategory.LITIGATION: (BusinessImpact.REGULATION, BusinessImpact.REPUTATION),
    EventCategory.MANAGEMENT_CHANGE: (BusinessImpact.MANAGEMENT,),
    EventCategory.PROMOTER_ACTIVITY: (BusinessImpact.MANAGEMENT,),
    EventCategory.SHARE_BUYBACK: (BusinessImpact.BALANCE_SHEET,),
    EventCategory.DIVIDEND: (BusinessImpact.CASH_FLOW,),
    EventCategory.FUNDRAISING: (BusinessImpact.BALANCE_SHEET,),
    EventCategory.DEBT: (BusinessImpact.DEBT, BusinessImpact.BALANCE_SHEET),
    EventCategory.CREDIT_RATING: (BusinessImpact.DEBT, BusinessImpact.BALANCE_SHEET),
    EventCategory.SUPPLY_CHAIN: (BusinessImpact.SUPPLY, BusinessImpact.INPUT_COST),
    EventCategory.RAW_MATERIAL: (BusinessImpact.INPUT_COST, BusinessImpact.MARGIN),
    EventCategory.COMMODITY: (BusinessImpact.PRICING, BusinessImpact.INPUT_COST),
    EventCategory.COMPETITOR: (BusinessImpact.COMPETITION,),
    EventCategory.CUSTOMER: (BusinessImpact.CUSTOMERS,),
    EventCategory.CYBERSECURITY: (BusinessImpact.REPUTATION,),
    EventCategory.FRAUD: (BusinessImpact.REPUTATION, BusinessImpact.MANAGEMENT),
    EventCategory.GOVERNMENT_POLICY: (BusinessImpact.REGULATION, BusinessImpact.DEMAND),
    EventCategory.TENDER: (BusinessImpact.REVENUE,),
    EventCategory.GEOPOLITICAL: (BusinessImpact.SUPPLY, BusinessImpact.GEOGRAPHY),
    EventCategory.MACRO: (BusinessImpact.DEMAND,),
    EventCategory.INTEREST_RATE: (BusinessImpact.MARGIN, BusinessImpact.DEMAND),
    EventCategory.CURRENCY: (BusinessImpact.REVENUE, BusinessImpact.MARGIN),
    EventCategory.INDUSTRY_DEMAND: (BusinessImpact.DEMAND,),
    EventCategory.TECHNOLOGY: (BusinessImpact.PRODUCT, BusinessImpact.COMPETITION),
    EventCategory.ANALYST_ACTION: (BusinessImpact.REPUTATION,),
}

# Time horizon by category. Anything unlisted resolves to SHORT_TERM or UNKNOWN.
CATEGORY_HORIZON: Dict[EventCategory, TimeHorizon] = {
    EventCategory.EARNINGS: TimeHorizon.IMMEDIATE,
    EventCategory.DIVIDEND: TimeHorizon.IMMEDIATE,
    EventCategory.ANALYST_ACTION: TimeHorizon.IMMEDIATE,
    EventCategory.CYBERSECURITY: TimeHorizon.IMMEDIATE,
    EventCategory.FRAUD: TimeHorizon.IMMEDIATE,
    EventCategory.COMMODITY: TimeHorizon.SHORT_TERM,
    EventCategory.CURRENCY: TimeHorizon.SHORT_TERM,
    EventCategory.INTEREST_RATE: TimeHorizon.SHORT_TERM,
    EventCategory.SUPPLY_CHAIN: TimeHorizon.SHORT_TERM,
    EventCategory.RAW_MATERIAL: TimeHorizon.SHORT_TERM,
    EventCategory.ORDER_WIN: TimeHorizon.MEDIUM_TERM,
    EventCategory.EXPORT_ORDER: TimeHorizon.MEDIUM_TERM,
    EventCategory.CONTRACT_WIN: TimeHorizon.MEDIUM_TERM,
    EventCategory.GUIDANCE: TimeHorizon.MEDIUM_TERM,
    EventCategory.TARIFF: TimeHorizon.MEDIUM_TERM,
    EventCategory.TRADE_POLICY: TimeHorizon.MEDIUM_TERM,
    EventCategory.REGULATORY: TimeHorizon.MEDIUM_TERM,
    EventCategory.GOVERNMENT_POLICY: TimeHorizon.MEDIUM_TERM,
    EventCategory.INDUSTRY_DEMAND: TimeHorizon.MEDIUM_TERM,
    EventCategory.CAPACITY_EXPANSION: TimeHorizon.LONG_TERM,
    EventCategory.NEW_PLANT: TimeHorizon.LONG_TERM,
    EventCategory.CAPEX: TimeHorizon.LONG_TERM,
    EventCategory.ACQUISITION: TimeHorizon.LONG_TERM,
    EventCategory.MERGER: TimeHorizon.LONG_TERM,
    EventCategory.TECHNOLOGY: TimeHorizon.LONG_TERM,
    EventCategory.MANAGEMENT_CHANGE: TimeHorizon.LONG_TERM,
}


@dataclass
class ScoreInput:
    """Everything the scorer is allowed to look at, in one place."""

    event: Event
    link: StockLink
    classification: Classification
    profile: CompanyProfile
    text: str = ""
    headline: str = ""
    source_types: Tuple[SourceType, ...] = ()
    independent_sources: int = 1
    is_update: bool = False


def has_magnitude(text: str) -> bool:
    """Does the text quantify the development at all?"""
    folded = fold(text)
    if _PERCENT.search(folded):
        return True
    if contains_any(folded, MAGNITUDE_WORDS):
        return True
    return bool(_NUMBER.search(folded)) and contains_any(folded, MAGNITUDE_UNITS)


def score_impact(data: ScoreInput) -> Tuple[int, List[str]]:
    """Return ``(impact_score, reasons)``. Additive, clamped, always explained."""
    categories: Set[EventCategory] = set(data.classification.categories) | set(
        data.event.event_types
    )
    relationship = data.link.relationship
    reasons: List[str] = []
    score = 0

    def add(points: int, reason: str) -> None:
        nonlocal score
        score += points
        reasons.append(f"{points:+d} {reason}")

    direct = relationship == Relationship.DIRECT
    major = has_magnitude(f"{data.headline} {data.text}")

    # ---- positive signals ---------------------------------------------
    if direct:
        add(5, "Direct company event")
    else:
        # Relationship strength is itself a signal: a specific, named exposure
        # deserves more attention than a shared macro condition.
        baseline = {
            Relationship.INDIRECT_STRONG: (3, "Close, specific exposure to this company"),
            Relationship.INDIRECT: (2, "Indirect but real exposure"),
            Relationship.SECTOR: (1, "Industry-level relevance"),
            Relationship.MACRO: (1, "Economy-wide relevance"),
        }.get(relationship)
        if baseline:
            add(*baseline)

    surprising = contains_any(fold(f"{data.headline} {data.text}"), SURPRISE_WORDS)
    if categories & EARNINGS_CATEGORIES:
        if direct:
            add(5 if surprising else 3,
                "Earnings or guidance with a surprise" if surprising else "Earnings or guidance")
        else:
            add(2, "Earnings or guidance at a related company")

    if categories & ORDER_CATEGORIES:
        if direct:
            add(4 if major else 3, "Major order or contract" if major else "Order or contract")
        elif relationship.rank >= Relationship.INDIRECT.rank:
            add(2, "Order activity at a related company")

    if categories & REGULATORY_CATEGORIES and relationship.rank >= Relationship.INDIRECT.rank:
        add(4 if (direct or major) else 2, "Regulatory or legal action")

    if categories & DEAL_CATEGORIES and relationship.rank >= Relationship.INDIRECT.rank:
        add(4 if direct else 2, "Acquisition, merger or divestiture")

    if categories & CAPACITY_CATEGORIES and direct:
        add(4 if major else 2, "Capacity expansion or capital expenditure")

    if categories & PEOPLE_CATEGORIES and direct:
        add(4, "Senior management or promoter development")

    if categories & CUSTOMER_CATEGORIES and relationship.rank >= Relationship.INDIRECT.rank:
        add(4 if direct else 3, "Customer gained or lost")

    if categories & SUPPLY_CATEGORIES and major:
        add(4 if relationship.rank >= Relationship.INDIRECT_STRONG.rank else 2,
            "Severe supply-chain or raw-material disruption")

    if categories & TRADE_CATEGORIES:
        if relationship.rank >= Relationship.INDIRECT.rank:
            add(4, "Material international trade or tariff policy")
        else:
            add(2, "Trade policy development")

    if categories & COMMODITY_CATEGORIES and data.link.exposure_types():
        commodity = next(
            (e for e in data.link.exposures if e.exposure_type == ExposureType.COMMODITY),
            None,
        )
        if commodity is not None and major:
            add(3, "Price shock in a commodity this company is exposed to")
            if "in headline" in commodity.detail and data.profile.commodity_role(
                commodity.term
            ) in {"input", "output"}:
                add(1, f"The commodity ({commodity.term}) is the subject of the headline")
        elif commodity is not None:
            add(2, "Movement in a relevant commodity")

    # A tender win and an order win are usually the same development described
    # twice; scoring both would double-count it.
    if (
        categories & POLICY_CATEGORIES
        and not (categories & ORDER_CATEGORIES)
        and relationship.rank >= Relationship.SECTOR.rank
    ):
        add(3, "Government policy or tender development")

    exposure_types = set(data.link.exposure_types())
    if ExposureType.COMPETITOR in exposure_types and (categories & (ORDER_CATEGORIES | CAPACITY_CATEGORIES | DEAL_CATEGORIES)):
        add(3, "Strategically important competitor development")

    named_chain = [
        e for e in data.link.exposures
        if e.exposure_type in {ExposureType.CUSTOMER, ExposureType.SUPPLIER}
        and is_named_entity(e.term)
    ]
    if named_chain and not direct and categories & (
        MATERIAL_CATEGORIES_FOR_CHAIN | SUPPLY_CATEGORIES | ORDER_CATEGORIES
    ):
        add(3, f"Development at a known counterparty ({named_chain[0].term})")

    if any(t == SourceType.COMPANY_EXCHANGE_FILING for t in data.source_types):
        add(3, "Official exchange announcement")
    elif any(t == SourceType.COMPANY_IR for t in data.source_types):
        add(2, "Company investor-relations release")

    if any(t == SourceType.REGULATOR for t in data.source_types):
        add(3, "Original regulator announcement")

    if data.event.is_international or data.link.international:
        add(2, "International market development")

    if data.independent_sources >= 3:
        add(2, f"{data.independent_sources} independent sources")

    if direct and data.link.entity is not None and data.link.entity.in_headline:
        add(2, "Company appears in the headline")

    if _key_geography(data):
        add(2, "Names a key geography for this company")

    if categories & INDUSTRY_CATEGORIES and major and relationship.rank >= Relationship.SECTOR.rank:
        add(2, "Major industry-level development")

    if categories & CRISIS_CATEGORIES and direct:
        add(4, "Fraud, cyber or product-failure incident")

    # ---- negative signals ----------------------------------------------
    if relationship == Relationship.INDIRECT and data.link.evidence_weight < 2.5:
        add(-2, "Weak indirect connection")

    official_language = data.classification.official_language or any(
        t in {SourceType.REGULATOR, SourceType.COMPANY_EXCHANGE_FILING, SourceType.COMPANY_IR}
        for t in data.source_types
    )
    if (
        not direct
        and relationship.rank <= Relationship.SECTOR.rank
        and not major
        and not official_language
    ):
        add(-3, "Passing mention without a quantified development")

    if data.classification.speculative:
        add(-3, "Speculative or unconfirmed report")

    if data.classification.opinion:
        add(-4, "Opinion, preview or stock-tip piece")

    if data.classification.routine_release:
        add(-6, "Scheduled statistical release, not a development")

    if data.classification.market_chatter:
        add(-5, "Market commentary rather than a company development")

    if data.is_update:
        add(-2, "Story already reported; this is a repeat")

    if categories and categories <= SCHEDULED_CATEGORIES and not surprising:
        add(-2, "Scheduled disclosure with no surprise")

    if all(t in {SourceType.BLOG, SourceType.SOCIAL_MEDIA} for t in data.source_types) and data.source_types:
        add(-4, "Only low-quality sources")

    # A big claim carried by one unverified outlet is not a big event yet.
    official_source = any(
        t in {SourceType.COMPANY_EXCHANGE_FILING, SourceType.REGULATOR, SourceType.COMPANY_IR}
        for t in data.source_types
    )
    if data.independent_sources == 1 and not official_source:
        weak_source = all(
            t in {SourceType.UNKNOWN_NEWS_SITE, SourceType.BLOG, SourceType.SOCIAL_MEDIA}
            for t in data.source_types
        )
        add(-2 if weak_source else -1,
            "Single unverified source" if weak_source else "Single source, unconfirmed")

    # ---- caps ------------------------------------------------------------
    score = max(IMPACT_SCORE_MIN, min(IMPACT_SCORE_MAX, score))
    return score, reasons


def apply_relationship_caps(
    score: int, relationship: Relationship, config_caps: Dict[str, int], reasons: List[str]
) -> int:
    """Broad relationships cannot produce company-level impact scores."""
    macro_cap = int(config_caps.get("macro_relationship_cap", 8))
    sector_cap = int(config_caps.get("sector_relationship_cap", 9))
    if relationship == Relationship.MACRO and score > macro_cap:
        reasons.append(f"capped at {macro_cap} (macro relationship)")
        return macro_cap
    if relationship == Relationship.SECTOR and score > sector_cap:
        reasons.append(f"capped at {sector_cap} (sector relationship)")
        return sector_cap
    if relationship == Relationship.WEAK:
        return min(score, 9)
    return score


def _key_geography(data: ScoreInput) -> bool:
    markets = {m.lower() for m in data.profile.export_markets}
    if not markets:
        return False
    return any(
        e.exposure_type == ExposureType.GEOGRAPHY and e.term.lower() in markets
        for e in data.link.exposures
    )


def score_confidence(data: ScoreInput, quality_map: Dict[str, int]) -> Tuple[float, List[str]]:
    """How much should this assessment be trusted? Separate from importance."""
    reasons: List[str] = []
    qualities = [quality_map.get(t.value, 3) for t in data.source_types] or [3]
    best = max(qualities)

    confidence = 0.20 + (best / 10.0) * 0.35
    reasons.append(f"best source quality {best}/10")

    if data.independent_sources >= 3:
        confidence += 0.12
        reasons.append(f"{data.independent_sources} independent sources")
    elif data.independent_sources == 2:
        confidence += 0.06
        reasons.append("two independent sources")

    if any(t in {SourceType.COMPANY_EXCHANGE_FILING, SourceType.REGULATOR} for t in data.source_types):
        confidence += 0.12
        reasons.append("official confirmation available")

    relationship_bonus = {
        Relationship.DIRECT: 0.15,
        Relationship.INDIRECT_STRONG: 0.09,
        Relationship.INDIRECT: 0.05,
        Relationship.SECTOR: 0.02,
        Relationship.MACRO: 0.0,
        Relationship.WEAK: -0.08,
    }[data.link.relationship]
    confidence += relationship_bonus
    reasons.append(f"{data.link.relationship.value.lower().replace('_', ' ')} relationship")

    if has_magnitude(f"{data.headline} {data.text}"):
        confidence += 0.06
        reasons.append("financial implication is quantified")
    else:
        confidence -= 0.05
        reasons.append("no quantified financial implication")

    if data.classification.speculative:
        confidence -= 0.15
        reasons.append("speculative language")
    if data.classification.opinion:
        confidence -= 0.10
        reasons.append("opinion piece")

    # Never 100%: this is an automated read of headlines, not a fact check.
    return round(max(0.05, min(0.95, confidence)), 2), reasons


def business_impacts(
    categories: Sequence[EventCategory], link: StockLink, profile: CompanyProfile
) -> List[BusinessImpact]:
    """Which parts of the business an event could touch."""
    out: List[BusinessImpact] = []
    for category in categories:
        for impact in CATEGORY_IMPACTS.get(category, ()):
            if impact not in out:
                out.append(impact)

    exposure_types = set(link.exposure_types())
    if ExposureType.COMPETITOR in exposure_types and BusinessImpact.COMPETITION not in out:
        out.append(BusinessImpact.COMPETITION)
    if ExposureType.CUSTOMER in exposure_types and BusinessImpact.CUSTOMERS not in out:
        out.append(BusinessImpact.CUSTOMERS)
    if ExposureType.SUPPLIER in exposure_types and BusinessImpact.SUPPLY not in out:
        out.append(BusinessImpact.SUPPLY)
    if ExposureType.CURRENCY in exposure_types and BusinessImpact.REVENUE not in out:
        out.append(BusinessImpact.REVENUE)

    for exposure in link.exposures:
        if exposure.exposure_type != ExposureType.COMMODITY:
            continue
        role = profile.commodity_role(exposure.term)
        target = BusinessImpact.INPUT_COST if role == "input" else BusinessImpact.PRICING
        if target not in out:
            out.append(target)

    if profile.export_markets and any(
        e.exposure_type == ExposureType.GEOGRAPHY
        and e.term.lower() in {m.lower() for m in profile.export_markets}
        for e in link.exposures
    ):
        if BusinessImpact.EXPORTS not in out:
            out.append(BusinessImpact.EXPORTS)

    return out[:6]


def time_horizon(categories: Sequence[EventCategory], relationship: Relationship) -> TimeHorizon:
    """Earliest-binding horizon across the event's categories."""
    order = [
        TimeHorizon.IMMEDIATE,
        TimeHorizon.SHORT_TERM,
        TimeHorizon.MEDIUM_TERM,
        TimeHorizon.LONG_TERM,
    ]
    found = [CATEGORY_HORIZON[c] for c in categories if c in CATEGORY_HORIZON]
    if not found:
        return TimeHorizon.UNKNOWN
    # A macro or sector link rarely bites immediately.
    horizon = min(found, key=order.index)
    if relationship in {Relationship.MACRO, Relationship.SECTOR} and horizon == TimeHorizon.IMMEDIATE:
        return TimeHorizon.SHORT_TERM
    return horizon


# --------------------------------------------------------------------------
# Narrative helpers
#
# These produce the "why it matters" line and the "watch next" checklist
# deterministically, so the report is useful with AI switched off.
# --------------------------------------------------------------------------

CATEGORY_WATCH: Dict[EventCategory, Tuple[str, ...]] = {
    EventCategory.ORDER_WIN: (
        "order execution schedule", "customer identity and concentration",
        "realised margin on the order", "whether extra capacity is needed",
    ),
    EventCategory.EXPORT_ORDER: (
        "shipment timeline", "currency hedging on the contract",
        "destination-market duties", "repeat-order potential",
    ),
    EventCategory.EARNINGS: (
        "margin trend versus the previous quarter", "management commentary on demand",
        "order book or loan book disclosure", "any guidance change",
    ),
    EventCategory.GUIDANCE: ("assumptions behind the guidance", "execution track record"),
    EventCategory.CAPACITY_EXPANSION: (
        "funding mix for the expansion", "commissioning timeline",
        "demand visibility for the new capacity", "effect on return ratios",
    ),
    EventCategory.NEW_PLANT: (
        "capital outlay and funding", "commissioning date", "utilisation ramp",
    ),
    EventCategory.REGULATORY: (
        "formal order or circular text", "compliance deadline",
        "company response or clarification", "precedent for peers",
    ),
    EventCategory.TARIFF: (
        "duty rate and effective date", "exemptions and carve-outs",
        "competitor exposure to the same duty", "pass-through to customers",
    ),
    EventCategory.TRADE_POLICY: (
        "scope of the measure", "affected product codes", "retaliation risk",
    ),
    EventCategory.COMMODITY: (
        "whether the price move persists", "contract repricing lag",
        "inventory position", "hedging disclosure",
    ),
    EventCategory.SUPPLY_CHAIN: (
        "duration of the disruption", "alternate sourcing",
        "inventory cover", "cost pass-through",
    ),
    EventCategory.INTEREST_RATE: (
        "deposit repricing lag", "loan book mix (fixed versus floating)",
        "credit demand response", "margin guidance",
    ),
    EventCategory.MANAGEMENT_CHANGE: (
        "reason for the change", "successor's background", "further exits",
    ),
    EventCategory.CREDIT_RATING: ("rationale in the rating note", "covenant implications"),
    EventCategory.GOVERNMENT_POLICY: (
        "notification text and timelines", "budget or allocation attached",
        "which players qualify",
    ),
    EventCategory.TENDER: ("bid outcome", "order value", "competing bidders"),
    EventCategory.COMPETITOR: (
        "pricing response", "market-share shift", "capacity announcements",
    ),
    EventCategory.FRAUD: ("regulatory response", "quantum involved", "auditor commentary"),
    EventCategory.CYBERSECURITY: ("data affected", "regulatory notification", "service restoration"),
}

RELATIONSHIP_PHRASE: Dict[Relationship, str] = {
    Relationship.DIRECT: "This is news about the company itself",
    Relationship.INDIRECT_STRONG: "This reaches the company through a close, specific exposure",
    Relationship.INDIRECT: "This reaches the company indirectly",
    Relationship.SECTOR: "This is industry-level news rather than company news",
    Relationship.MACRO: "This is an economy-wide development the company shares with others",
    Relationship.WEAK: "The connection to the company is tenuous",
}


def watch_next_items(
    categories: Sequence[EventCategory], link: StockLink, limit: int = 4
) -> List[str]:
    """A short, concrete checklist for following the story."""
    items: List[str] = []
    for category in categories:
        for item in CATEGORY_WATCH.get(category, ()):
            if item not in items:
                items.append(item)

    for exposure in link.exposures[:2]:
        if exposure.exposure_type == ExposureType.COMPETITOR:
            candidate = f"{exposure.term}'s follow-up announcements"
        elif exposure.exposure_type == ExposureType.CUSTOMER:
            candidate = f"order flow from {exposure.term}"
        elif exposure.exposure_type == ExposureType.SUPPLIER:
            candidate = f"supply terms from {exposure.term}"
        elif exposure.exposure_type == ExposureType.COMMODITY:
            candidate = f"{exposure.term} over the next few weeks"
        else:
            continue
        if candidate not in items:
            items.append(candidate)

    if not items:
        items.append("confirmation from an official or company source")
    return items[:limit]


def why_it_matters(
    link: StockLink,
    categories: Sequence[EventCategory],
    impacts: Sequence[BusinessImpact],
    profile: CompanyProfile,
) -> str:
    """One or two plain sentences explaining the connection."""
    opening = RELATIONSHIP_PHRASE.get(link.relationship, "This may affect the company")

    if link.relationship == Relationship.DIRECT:
        detail = ""
    else:
        terms = [e.term for e in link.exposures[:2]]
        if terms:
            kinds = {e.exposure_type.value.lower().replace("_", " ") for e in link.exposures[:2]}
            detail = f" via its {', '.join(sorted(kinds))} exposure to {', and '.join(terms)}"
        else:
            detail = ""

    parts = [f"{opening}{detail}."]
    if impacts:
        readable = ", ".join(i.value.lower().replace("_", " ") for i in impacts[:3])
        parts.append(f"The parts of the business most likely touched are {readable}.")
    if categories:
        label = categories[0].value.lower().replace("_", " ")
        parts.append(f"Classified as {label}.")
    return " ".join(parts)
