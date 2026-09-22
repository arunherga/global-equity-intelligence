"""Direction analysis: positive, negative, mixed, neutral or uncertain.

Direction is deliberately separate from impact. The system does not force a
sentiment: a ₹3,000 crore factory is high impact and genuinely UNCERTAIN,
because it is growth *and* capital intensity. A rate cut is MIXED for a bank:
margins compress, credit demand improves.

The same headline flips sign depending on the relationship. "Module prices
collapse" is negative for a module maker and positive for a project developer;
"competitor wins a huge order" is negative for us. So direction is computed per
stock, never per article.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Set, Tuple

from .classify import Classification
from .matching import contains_any, contains_phrase, find_spans, fold
from .models import Direction, EventCategory, ExposureType, Relationship
from .profiles.loader import CompanyProfile
from .relationship import StockLink

# Words describing something going up / down. Used with the commodity role and
# the exposure type to work out what "up" means for this particular company.
UP_WORDS = (
    "rises", "rise", "rose", "jumps", "jump", "surges", "surge", "climbs",
    "soars", "gains", "gain", "increases", "increase", "higher", "up",
    "rally", "rallies", "record high", "strengthens", "expands", "grows",
    "growth", "boost", "boosts", "improves", "recovery", "rebound",
)
DOWN_WORDS = (
    "falls", "fall", "fell", "drops", "drop", "slumps", "slump", "declines",
    "decline", "plunges", "plunge", "sinks", "tumbles", "slides", "lower",
    "down", "collapse", "collapses", "crash", "crashes", "weakens", "slows",
    "slowdown", "contraction", "shrinks", "cuts", "cut", "record low", "glut",
    "oversupply", "loss", "losses", "worsens",
)

GOOD_WORDS = (
    "approval", "approved", "approves", "nod", "clearance", "granted", "wins",
    "won", "bags", "secures", "upgrade", "upgraded", "beats", "outperforms",
    "profit rises", "strong demand", "record profit", "expands capacity",
    "resolved", "lifted", "exemption", "incentive", "subsidy", "relief",
)
BAD_WORDS = (
    "penalty", "fine", "fined", "warning letter", "form 483", "rejected",
    "rejection", "suspended", "suspension", "ban", "banned", "recall",
    "downgrade", "downgraded", "misses", "shortfall", "probe", "investigation",
    "raid", "default", "fraud", "strike", "shutdown", "halt", "halted",
    "disruption", "delay", "delayed", "cancelled", "canceled", "lawsuit",
    "breach", "outage", "restriction", "curb", "crackdown",
)

# Categories whose direction is inherently two-sided for the company.
AMBIGUOUS_CATEGORIES = {
    EventCategory.CAPACITY_EXPANSION,
    EventCategory.NEW_PLANT,
    EventCategory.CAPEX,
    EventCategory.ACQUISITION,
    EventCategory.MERGER,
    EventCategory.INVESTMENT,
    EventCategory.FUNDRAISING,
    EventCategory.MANAGEMENT_CHANGE,
    EventCategory.TARIFF,
    EventCategory.TRADE_POLICY,
    EventCategory.GEOPOLITICAL,
    EventCategory.TECHNOLOGY,
}

CLEARLY_POSITIVE_CATEGORIES = {
    EventCategory.ORDER_WIN,
    EventCategory.CONTRACT_WIN,
    EventCategory.EXPORT_ORDER,
    EventCategory.NEW_CUSTOMER,
    EventCategory.SHARE_BUYBACK,
    EventCategory.DIVIDEND,
    EventCategory.INSIDER_BUYING,
}

CLEARLY_NEGATIVE_CATEGORIES = {
    EventCategory.ORDER_LOSS,
    EventCategory.CONTRACT_LOSS,
    EventCategory.CUSTOMER_LOSS,
    EventCategory.PRODUCT_FAILURE,
    EventCategory.FRAUD,
    EventCategory.CYBERSECURITY,
    EventCategory.LITIGATION,
    EventCategory.INSIDER_SELLING,
}


@dataclass
class DirectionVote:
    direction: Direction
    reason: str
    strength: float = 1.0


def _nearest(text: str, words: Sequence[str], anchor: int) -> Optional[int]:
    """Distance from ``anchor`` to the closest of ``words``, if present."""
    best: Optional[int] = None
    for word in words:
        for start, _ in find_spans(text, word):
            distance = abs(start - anchor)
            if best is None or distance < best:
                best = distance
    return best


def _movement(text: str, anchor_term: str = "") -> Optional[str]:
    """Is the text about something going up or down?

    Headlines routinely contain both directions — "prices surge on supply
    cuts". When both appear, the one closer to the thing being measured wins,
    which is what a reader does instinctively.
    """
    up = contains_any(text, UP_WORDS)
    down = contains_any(text, DOWN_WORDS)
    if up and not down:
        return "up"
    if down and not up:
        return "down"
    if not (up and down):
        return None

    anchor = 0
    if anchor_term:
        spans = find_spans(text, anchor_term)
        if spans:
            anchor = spans[0][0]
    up_distance = _nearest(text, UP_WORDS, anchor)
    down_distance = _nearest(text, DOWN_WORDS, anchor)
    if up_distance is None or down_distance is None:
        return None
    if up_distance == down_distance:
        return None
    return "up" if up_distance < down_distance else "down"


def analyse_direction(
    headline: str,
    body: str,
    link: StockLink,
    classification: Classification,
    profile: CompanyProfile,
) -> Tuple[Direction, List[str]]:
    """Return ``(direction, reasons)`` for one company's view of one event."""
    text = fold(f"{headline} {body}")
    head = fold(headline)
    categories: Set[EventCategory] = set(classification.categories)
    votes: List[DirectionVote] = []
    direct = link.relationship == Relationship.DIRECT

    # ---- 1. Commodity exposure, read through the company's role ----------
    commodity_terms = [
        e.term for e in link.exposures if e.exposure_type == ExposureType.COMMODITY
    ]
    anchor = commodity_terms[0] if commodity_terms else ""
    move = _movement(head, anchor) or _movement(text, anchor)
    for exposure in link.exposures:
        if exposure.exposure_type != ExposureType.COMMODITY or move is None:
            continue
        role = profile.commodity_role(exposure.term)
        if role == "input":
            direction = Direction.NEGATIVE if move == "up" else Direction.POSITIVE
            votes.append(DirectionVote(
                direction,
                f"{exposure.term} is an input cost and is moving {move}",
                1.2,
            ))
        elif role == "output":
            direction = Direction.POSITIVE if move == "up" else Direction.NEGATIVE
            votes.append(DirectionVote(
                direction,
                f"{exposure.term} is a selling price and is moving {move}",
                1.2,
            ))
        else:
            votes.append(DirectionVote(
                Direction.UNCERTAIN,
                f"{exposure.term} is handled rather than bought or sold; "
                "volume matters more than price",
                0.6,
            ))
        break

    # ---- 2. Ecosystem: a rival's good news is our bad news ---------------
    if not direct:
        exposure_types = set(link.exposure_types())
        good = contains_any(head, GOOD_WORDS) or bool(categories & CLEARLY_POSITIVE_CATEGORIES)
        bad = contains_any(head, BAD_WORDS) or bool(categories & CLEARLY_NEGATIVE_CATEGORIES)

        if ExposureType.COMPETITOR in exposure_types and (good or bad):
            names = ", ".join(sorted({
                e.term for e in link.exposures if e.exposure_type == ExposureType.COMPETITOR
            })[:2])
            if good and not bad:
                votes.append(DirectionVote(
                    Direction.NEGATIVE, f"a competitor ({names}) is doing well", 1.0))
            elif bad and not good:
                votes.append(DirectionVote(
                    Direction.POSITIVE, f"a competitor ({names}) is under pressure", 1.0))

        if ExposureType.CUSTOMER in exposure_types and (good or bad):
            if good and not bad:
                votes.append(DirectionVote(
                    Direction.POSITIVE, "a customer's business is strengthening", 0.9))
            elif bad and not good:
                votes.append(DirectionVote(
                    Direction.NEGATIVE, "a customer is under pressure", 0.9))

        if ExposureType.SUPPLIER in exposure_types:
            if contains_any(text, ("disruption", "shutdown", "shortage", "halt", "strike")):
                votes.append(DirectionVote(
                    Direction.NEGATIVE, "supply from a known supplier is disrupted", 1.0))
            elif good:
                votes.append(DirectionVote(
                    Direction.POSITIVE, "a platform or supplier the company depends on is investing", 0.7))

    # ---- 3. Clear company-level categories -------------------------------
    if direct:
        if categories & CLEARLY_POSITIVE_CATEGORIES:
            votes.append(DirectionVote(
                Direction.POSITIVE,
                f"{sorted(c.value for c in categories & CLEARLY_POSITIVE_CATEGORIES)[0]} "
                "is favourable for the company",
                1.3,
            ))
        if categories & CLEARLY_NEGATIVE_CATEGORIES:
            votes.append(DirectionVote(
                Direction.NEGATIVE,
                f"{sorted(c.value for c in categories & CLEARLY_NEGATIVE_CATEGORIES)[0]} "
                "is unfavourable for the company",
                1.3,
            ))
        if contains_any(head, BAD_WORDS) and EventCategory.REGULATORY in categories:
            votes.append(DirectionVote(
                Direction.NEGATIVE, "adverse regulatory language in the headline", 1.1))
        elif contains_any(head, GOOD_WORDS) and EventCategory.REGULATORY in categories:
            votes.append(DirectionVote(
                Direction.POSITIVE, "favourable regulatory outcome", 1.1))

        if EventCategory.EARNINGS in categories and move:
            votes.append(DirectionVote(
                Direction.POSITIVE if move == "up" else Direction.NEGATIVE,
                f"reported results moving {move}",
                1.2,
            ))

    # ---- 4. Two-sided by nature ------------------------------------------
    if categories & AMBIGUOUS_CATEGORIES:
        label = sorted(c.value for c in categories & AMBIGUOUS_CATEGORIES)[0]
        votes.append(DirectionVote(
            Direction.UNCERTAIN,
            f"{label} cuts both ways — growth against capital, cost or execution risk",
            0.8,
        ))

    # Interest rates are the canonical two-sided case for a lender.
    if EventCategory.INTEREST_RATE in categories and _is_lender(profile):
        votes.append(DirectionVote(
            Direction.MIXED,
            "rate moves hit margins and credit demand in opposite directions",
            1.1,
        ))

    return _resolve(votes, categories, link)


def _is_lender(profile: CompanyProfile) -> bool:
    industry = (profile.industry or "").lower()
    return "bank" in industry or "financial" in industry


def _resolve(
    votes: Sequence[DirectionVote], categories: Set[EventCategory], link: StockLink
) -> Tuple[Direction, List[str]]:
    """Combine votes without forcing a sentiment."""
    if not votes:
        if link.relationship in {Relationship.MACRO, Relationship.SECTOR, Relationship.WEAK}:
            return Direction.NEUTRAL, ["No company-specific directional signal"]
        return Direction.UNCERTAIN, ["No clear directional signal in the available text"]

    weights: Dict[Direction, float] = {}
    for vote in votes:
        weights[vote.direction] = weights.get(vote.direction, 0.0) + vote.strength

    positive = weights.get(Direction.POSITIVE, 0.0)
    negative = weights.get(Direction.NEGATIVE, 0.0)
    mixed = weights.get(Direction.MIXED, 0.0)
    uncertain = weights.get(Direction.UNCERTAIN, 0.0)

    reasons = [v.reason for v in sorted(votes, key=lambda v: -v.strength)][:4]

    if positive and negative:
        reasons.insert(0, "Positive and negative effects both present")
        return Direction.MIXED, reasons
    if mixed >= max(positive, negative, uncertain):
        return Direction.MIXED, reasons
    if positive and positive > uncertain:
        return Direction.POSITIVE, reasons
    if negative and negative > uncertain:
        return Direction.NEGATIVE, reasons
    if uncertain:
        return Direction.UNCERTAIN, reasons
    return Direction.NEUTRAL, reasons
