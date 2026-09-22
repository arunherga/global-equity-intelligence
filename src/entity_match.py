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


@dataclass
class EntityMatch:
    """A confirmed mention of the company (or one of its subsidiaries)."""

    ticker: str
    alias: str
    strength: str                      # primary | secondary | subsidiary
    in_headline: bool = False
    spans: List[Tuple[int, int]] = field(default_factory=list)
    detail: str = ""

    @property
    def is_primary(self) -> bool:
        return self.strength in {"primary", "subsidiary"}


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

    # Explicit collector hints (exchange filings arrive pre-tagged).
    if profile.ticker in {t.upper() for t in article.tickers_hint}:
        return (
            EntityMatch(
                ticker=profile.ticker,
                alias=profile.ticker,
                strength="primary",
                in_headline=True,
                detail="tagged by collector",
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
        # Subsidiaries count as a direct mention of the parent.
        for subsidiary in profile.subsidiaries:
            spans = find_spans(text, subsidiary)
            if not spans:
                continue
            blocker = covered_by(spans[0], text, [n.lower() for n in profile.negative_aliases])
            if blocker:
                continue
            best = EntityMatch(
                ticker=profile.ticker,
                alias=subsidiary,
                strength="subsidiary",
                in_headline=bool(find_spans(headline, subsidiary)),
                spans=spans,
                detail=f"subsidiary '{subsidiary}'",
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
