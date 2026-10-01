"""Direct company identification.

This module answers one question: *is this article about the company itself?*
It is deliberately conservative. A false DIRECT match on a similarly named
company (Ravelcare Limited vs Ravel Electronics) is the most damaging error the
system can make, so every alias hit is checked against exclusion phrases.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .matching import contains_any, covered_by, find_spans, fold
from .models import Article
from .profiles.loader import AliasSpec, CompanyProfile

# Collectors that fetch per company and therefore genuinely know whose item
# this is - NSE, BSE and the IR pages are queried by scrip code. A search
# collector is not one of them, however confident its hint looks: its hint
# records which query found the item, not what the item is about.
TRUSTED_TICKER_COLLECTORS = frozenset({"nse", "bse", "company_ir"})


@dataclass
class EntityMatch:
    """A confirmed mention of the company (or one of its subsidiaries)."""

    ticker: str
    alias: str
    strength: str                # primary | secondary | subsidiary | brand
    in_headline: bool = False
    spans: List[Tuple[int, int]] = field(default_factory=list)
    detail: str = ""

    @property
    def is_primary(self) -> bool:
        # A brand is the name the company trades under, so a brand hit is
        # as direct as the listed name - not a hedged secondary match.
        return self.strength in {"primary", "subsidiary", "brand"}


@dataclass
class EntityRejection:
    """Recorded when an alias hit was deliberately discarded."""

    ticker: str
    alias: str
    reason: str
    blocker: str = ""


def _alias_hits(
    alias: AliasSpec,
    profile: CompanyProfile,
    text_folded: str,
    headline_folded: str,
) -> Tuple[List[Tuple[int, int]], Optional[EntityRejection]]:
    spans = find_spans(text_folded, alias.value)
    if not spans:
        return [], None

    blockers = list(alias.excludes) + [n.lower() for n in profile.negative_aliases]
    surviving: List[Tuple[int, int]] = []
    blocked_by = ""
    for span in spans:
        blocker = covered_by(span, text_folded, blockers) if blockers else None
        if blocker:
            blocked_by = blocker
            continue
        surviving.append(span)

    if not surviving:
        return [], EntityRejection(
            ticker=profile.ticker,
            alias=alias.value,
            reason="alias appears only inside an excluded phrase",
            blocker=blocked_by,
        )

    if alias.requires and not contains_any(text_folded, alias.requires):
        return [], EntityRejection(
            ticker=profile.ticker,
            alias=alias.value,
            reason="required context term absent",
            blocker=", ".join(alias.requires[:4]),
        )

    return surviving, None


def match_entity(
    article: Article,
    profile: CompanyProfile,
    text_folded: Optional[str] = None,
    headline_folded: Optional[str] = None,
) -> Tuple[Optional[EntityMatch], List[EntityRejection]]:
    """Best direct match for one company, plus any rejections (explainability)."""
    text = text_folded if text_folded is not None else fold(article.text)
    headline = headline_folded if headline_folded is not None else fold(article.title)

    rejections: List[EntityRejection] = []
    best: Optional[EntityMatch] = None

    # Explicit collector hints, but only from collectors that genuinely know.
    #
    # NSE, BSE and the IR pages are fetched per scrip code: if an item came
    # back from Coal India's filing feed it IS about Coal India. A search
    # collector knows no such thing - its hint records which query found the
    # item, and a search engine answers loosely.
    #
    # Honouring both equally was the worst precision bug in this system. It
    # returned a confirmed, headline-strength DIRECT match before any other
    # check ran, so on 2026-09-30 a Coal India query returning "thyssenkrupp
    # nucera wins chlor-alkali order from Hongniu Lanzhou in China" scored
    # 10/15 as a direct Coal India event, and an HDFC Bank query returning a
    # Power Mech Projects order scored 9/15. It also bypassed every guard
    # built to stop exactly that: the negative aliases, the requires/excludes
    # conditions, and the corroboration rule - all of them live below this
    # return.
    if article.collector in TRUSTED_TICKER_COLLECTORS or article.is_official:
        if profile.ticker in {t.upper() for t in article.tickers_hint}:
            return (
                EntityMatch(
                    ticker=profile.ticker,
                    alias=profile.ticker,
                    strength="primary",
                    in_headline=True,
                    detail=f"tagged by {article.collector or 'collector'}",
                ),
                rejections,
            )

    ordered = sorted(
        profile.aliases, key=lambda a: (0 if a.is_primary else 1, -len(a.value))
    )
    for alias in ordered:
        spans, rejection = _alias_hits(alias, profile, text, headline)
        if rejection:
            rejections.append(rejection)
            continue
        if not spans:
            continue
        in_headline = any(find_spans(headline, alias.value))
        candidate = EntityMatch(
            ticker=profile.ticker,
            alias=alias.value,
            strength="primary" if alias.is_primary else "secondary",
            in_headline=in_headline,
            spans=spans,
            detail=f"matched alias '{alias.value}'",
        )
        if best is None or _better(candidate, best):
            best = candidate
        if candidate.is_primary and candidate.in_headline:
            break

    if best is None:
        # Subsidiaries count as a direct mention of the parent, and so do
        # brands. A brand is the name the company trades under, and trade
        # press uses it in preference to the listed entity: "HexL wins a
        # USD 5m repeat order" is a Jinkushal story that names Jinkushal
        # nowhere. Brands were previously only used to generate queries, so
        # such an article was fetched and then dropped for having no match.
        for name, strength in (
            [(s, "subsidiary") for s in profile.subsidiaries]
            + [(b, "brand") for b in profile.brands]
        ):
            spans = find_spans(text, name)
            if not spans:
                continue
            blocker = covered_by(spans[0], text, [n.lower() for n in profile.negative_aliases])
            if blocker:
                continue
            best = EntityMatch(
                ticker=profile.ticker,
                alias=name,
                strength=strength,
                in_headline=bool(find_spans(headline, name)),
                spans=spans,
                detail=f"{strength} '{name}'",
            )
            break

    return best, rejections


def _better(candidate: EntityMatch, current: EntityMatch) -> bool:
    key = lambda m: (m.is_primary, m.in_headline, len(m.alias))  # noqa: E731
    return key(candidate) > key(current)


def match_all_entities(
    article: Article, profiles: List[CompanyProfile]
) -> Tuple[List[EntityMatch], List[EntityRejection]]:
    text = fold(article.text)
    headline = fold(article.title)
    matches: List[EntityMatch] = []
    rejections: List[EntityRejection] = []
    for profile in profiles:
        match, rejected = match_entity(article, profile, text, headline)
        rejections.extend(rejected)
        if match:
            matches.append(match)
    return matches, rejections
