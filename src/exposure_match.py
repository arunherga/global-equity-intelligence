"""Exposure matching: the reason this is not an RSS reader.

A headline like *"China cuts solar export rebates"* never mentions Waaree, and
*"Newcastle thermal coal collapses"* never mentions Coal India. Entity matching
finds nothing in either case. Exposure matching asks a different question: does
this article touch something the company is *exposed to* — a competitor, a
customer, an input commodity, a regulator, an export market, a sector topic?

Every hit is recorded with the term that produced it, so the report can always
say why a company appeared under a piece of foreign news.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .matching import contains_phrase, covered_by, find_spans, fold
from .models import Article, ExposureMatch, ExposureType, Relationship
from .profiles.loader import CompanyProfile

# What each exposure type implies *on its own*, before any upgrades.
BASE_RELATIONSHIP: Dict[ExposureType, Relationship] = {
    ExposureType.COMPANY: Relationship.DIRECT,
    ExposureType.SUBSIDIARY: Relationship.DIRECT,
    ExposureType.PRODUCT: Relationship.SECTOR,
    ExposureType.CUSTOMER: Relationship.INDIRECT,
    ExposureType.SUPPLIER: Relationship.INDIRECT,
    ExposureType.COMPETITOR: Relationship.INDIRECT,
    ExposureType.INDUSTRY: Relationship.SECTOR,
    ExposureType.COMMODITY: Relationship.INDIRECT,
    ExposureType.CURRENCY: Relationship.MACRO,
    ExposureType.REGULATION: Relationship.INDIRECT,
    ExposureType.GEOGRAPHY: Relationship.WEAK,
    ExposureType.MACRO: Relationship.MACRO,
    ExposureType.TECHNOLOGY: Relationship.SECTOR,
}

# Evidence weights. Specific, checkable things outrank broad ones.
BASE_WEIGHT: Dict[ExposureType, float] = {
    ExposureType.SUBSIDIARY: 3.0,
    ExposureType.CUSTOMER: 2.0,
    ExposureType.SUPPLIER: 2.0,
    ExposureType.COMPETITOR: 2.0,
    ExposureType.COMMODITY: 2.0,
    ExposureType.REGULATION: 1.8,
    ExposureType.INDUSTRY: 1.5,
    ExposureType.PRODUCT: 1.2,
    ExposureType.TECHNOLOGY: 1.0,
    ExposureType.MACRO: 1.0,
    ExposureType.CURRENCY: 0.8,
    ExposureType.GEOGRAPHY: 0.4,
    ExposureType.COMPANY: 4.0,
}

# Geography on its own means nothing — half the business press mentions India.
# It only earns weight when it is an export market and something else matched.
GENERIC_GEOGRAPHIES = {"india", "asia", "europe", "world", "global"}

# A single word naming the company's own sector ("solar", "coal", "banking")
# is real evidence, but only ever sector-level: half the business press uses
# these words. Multi-word terms are specific enough to stand on their own.
SECTOR_CAPPED_TYPES = {
    ExposureType.INDUSTRY,
    ExposureType.PRODUCT,
    ExposureType.TECHNOLOGY,
}

MAX_EXPOSURES_PER_TYPE = 4

# Marker written into ExposureMatch.detail for foreign industry/trade topics.
INTERNATIONAL_DETAIL = "international topic"

# Marker for a generic role ("distributors", "retail depositors") as opposed to
# a named counterparty ("Premier Energies"). Profiles capitalise real names, so
# an all-lowercase term is a role, and a role is not a specific exposure.
GENERIC_ROLE_DETAIL = "generic role"

ECOSYSTEM_TYPES = {
    ExposureType.CUSTOMER,
    ExposureType.SUPPLIER,
    ExposureType.COMPETITOR,
}


def is_named_entity(term: str) -> bool:
    """A named counterparty, rather than a description of a kind of one."""
    words = term.split()
    return bool(words) and any(w[:1].isupper() for w in words)


@dataclass
class ExposureResult:
    """All exposure evidence linking one article to one company."""

    ticker: str
    matches: List[ExposureMatch]

    @property
    def total_weight(self) -> float:
        return sum(m.weight for m in self.matches)

    @property
    def types(self) -> List[ExposureType]:
        seen: List[ExposureType] = []
        for match in self.matches:
            if match.exposure_type not in seen:
                seen.append(match.exposure_type)
        return seen

    def strongest(self) -> Relationship:
        return Relationship.strongest([m.relationship for m in self.matches])

    def has(self, exposure_type: ExposureType) -> bool:
        return any(m.exposure_type == exposure_type for m in self.matches)

    def terms_for(self, exposure_type: ExposureType) -> List[str]:
        return [m.term for m in self.matches if m.exposure_type == exposure_type]


def _blocked(text_folded: str, term: str, blockers: Sequence[str]) -> bool:
    if not blockers:
        return False
    spans = find_spans(text_folded, term)
    if not spans:
        return True
    return all(covered_by(span, text_folded, blockers) for span in spans)


def match_exposures(
    article: Article,
    profile: CompanyProfile,
    text_folded: Optional[str] = None,
    headline_folded: Optional[str] = None,
) -> ExposureResult:
    """Every exposure term from the profile that appears in the article."""
    text = text_folded if text_folded is not None else fold(article.text)
    headline = headline_folded if headline_folded is not None else fold(article.title)
    blockers = [n.lower() for n in profile.negative_aliases]

    matches: List[ExposureMatch] = []
    lexicon = profile.lexicon()

    for exposure_type, terms in lexicon.items():
        found = 0
        # Longer terms first: "solar module prices" beats "solar".
        for term in sorted({t for t in terms if t}, key=lambda t: (-len(t), t.lower())):
            if found >= MAX_EXPOSURES_PER_TYPE:
                break
            lowered = term.lower()
            if not contains_phrase(text, term):
                continue
            if blockers and _blocked(text, term, blockers):
                continue

            weight = BASE_WEIGHT.get(exposure_type, 1.0)
            relationship = BASE_RELATIONSHIP.get(exposure_type, Relationship.WEAK)
            detail = ""

            if exposure_type == ExposureType.GEOGRAPHY:
                if lowered in GENERIC_GEOGRAPHIES:
                    weight = 0.2
                elif lowered in {m.lower() for m in profile.export_markets}:
                    weight = 0.8
                    detail = "export market"

            if exposure_type == ExposureType.INDUSTRY and profile.is_international_topic(term):
                # The whole point of the system: foreign industry and trade
                # news that never names the company.
                weight += 0.3
                detail = INTERNATIONAL_DETAIL

            if exposure_type == ExposureType.COMMODITY:
                role = profile.commodity_role(term)
                detail = f"{role} commodity"
                # A named input/output commodity is a real, checkable exposure.
                weight += 0.5 if role in {"input", "output"} else 0.0

            if exposure_type == ExposureType.SUBSIDIARY:
                if lowered in {a.lower() for a in profile.associates}:
                    relationship = Relationship.INDIRECT_STRONG
                    detail = "group company, separately reported"
                else:
                    detail = "subsidiary"

            # Anything named in the headline is more likely to be the subject.
            if contains_phrase(headline, term):
                weight += 0.5
                detail = (detail + "; in headline").lstrip("; ")

            # ---- caps, applied last so no bonus can escape them ----------
            if exposure_type in ECOSYSTEM_TYPES and not is_named_entity(term):
                # "a distributor placed an order" is not news about a known
                # counterparty; it is a description of the industry.
                weight = min(weight, 1.0)
                relationship = Relationship.SECTOR
                detail = (GENERIC_ROLE_DETAIL + "; " + detail).rstrip("; ")

            # A one-word sector term is sector news at best.
            if exposure_type in SECTOR_CAPPED_TYPES and len(lowered.split()) == 1:
                weight = min(weight, 0.9)
                relationship = Relationship.SECTOR

            matches.append(
                ExposureMatch(
                    exposure_type=exposure_type,
                    term=term,
                    relationship=relationship,
                    weight=round(weight, 2),
                    detail=detail,
                )
            )
            found += 1

    matches.sort(key=lambda m: (-m.weight, m.exposure_type.value, m.term))
    return ExposureResult(ticker=profile.ticker, matches=matches)


def match_all_exposures(
    article: Article, profiles: Sequence[CompanyProfile]
) -> Dict[str, ExposureResult]:
    text = fold(article.text)
    headline = fold(article.title)
    results: Dict[str, ExposureResult] = {}
    for profile in profiles:
        result = match_exposures(article, profile, text, headline)
        if result.matches:
            results[profile.ticker] = result
    return results
