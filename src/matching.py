"""Low-level text matching primitives shared by every matcher.

Deliberately dependency-free and deterministic so the whole matching layer can
be unit-tested offline.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# Words too common to carry meaning when comparing titles.
STOPWORDS: Set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "is", "it", "its", "of", "on", "or", "that", "the", "to", "was", "were",
    "will", "with", "after", "over", "amid", "says", "said", "new", "may", "up",
    "down", "into", "out", "than", "this", "these", "their", "they", "but", "not",
}

_PUNCT = re.compile(r"[^\w\s%/&.+-]", re.UNICODE)
_WS = re.compile(r"\s+")


def strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch)
    )


def fold(text: str) -> str:
    """Lowercase, de-accent and squash whitespace/punctuation for matching."""
    if not text:
        return ""
    lowered = strip_accents(str(text)).lower()
    lowered = lowered.replace("&amp;", "&").replace("&#39;", "'").replace("&quot;", '"')
    lowered = lowered.replace("’", "'").replace("‘", "'")
    lowered = lowered.replace("–", "-").replace("—", "-")
    lowered = _PUNCT.sub(" ", lowered)
    return _WS.sub(" ", lowered).strip()


def tokens(text: str) -> List[str]:
    return [t for t in fold(text).split(" ") if t]


def content_tokens(text: str) -> List[str]:
    return [t for t in tokens(text) if t not in STOPWORDS and len(t) > 2]


# Words whose trailing "s" is part of the word, not a plural.
_NEVER_SINGULAR = ("ss", "us", "is", "as", "os", "ys")


def _stem(word: str) -> str:
    """Crudely singularise so "module" and "modules" match each other."""
    if len(word) > 3 and word.endswith("s") and not word.endswith(_NEVER_SINGULAR):
        return word[:-1]
    return word


@lru_cache(maxsize=4096)
def _phrase_pattern(phrase: str) -> Optional[re.Pattern]:
    """Compile a word-boundary, plural-tolerant regex for a phrase.

    Every word is reduced to a crude stem and allowed an optional trailing
    "s", so a profile term of "solar module" matches a headline about "solar
    modules" (and the reverse) without a stemming library. Word boundaries are
    lookarounds rather than \b, which does not behave after symbols like "/".
    """
    folded = fold(phrase)
    if not folded:
        return None
    parts = [rf"{re.escape(_stem(p))}s?" for p in folded.split(" ") if p]
    if not parts:
        return None
    body = r"[\s\-/]+".join(parts)
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


def find_spans(haystack_folded: str, phrase: str) -> List[Tuple[int, int]]:
    """All match spans of ``phrase`` inside an already-folded haystack."""
    pattern = _phrase_pattern(phrase)
    if pattern is None or not haystack_folded:
        return []
    return [(m.start(), m.end()) for m in pattern.finditer(haystack_folded)]


def contains_phrase(haystack_folded: str, phrase: str) -> bool:
    return bool(find_spans(haystack_folded, phrase))


def contains_any(haystack_folded: str, phrases: Iterable[str]) -> bool:
    return any(contains_phrase(haystack_folded, p) for p in phrases)


def first_match(haystack_folded: str, phrases: Iterable[str]) -> Optional[str]:
    for phrase in phrases:
        if contains_phrase(haystack_folded, phrase):
            return phrase
    return None


def matched_terms(haystack_folded: str, phrases: Iterable[str]) -> List[str]:
    """Every phrase present, longest first (longer phrases are more specific)."""
    hits = [p for p in phrases if contains_phrase(haystack_folded, p)]
    hits.sort(key=lambda p: (-len(p), p.lower()))
    return hits


def spans_overlap(inner: Tuple[int, int], outer: Tuple[int, int]) -> bool:
    return inner[0] >= outer[0] and inner[1] <= outer[1]


def covered_by(
    span: Tuple[int, int], haystack_folded: str, blocking_phrases: Iterable[str]
) -> Optional[str]:
    """Return the blocking phrase that swallows ``span``, if any.

    This is what keeps ``Ravel`` inside ``Ravel Electronics`` from being read
    as a mention of Ravelcare Limited.
    """
    for phrase in blocking_phrases:
        for outer in find_spans(haystack_folded, phrase):
            if spans_overlap(span, outer):
                return phrase
    return None


# -- canonicalisation -------------------------------------------------------
# Headlines about the same event use different verbs. Normalising them makes
# token overlap a far better clustering signal than raw words.
SYNONYMS: Dict[str, str] = {
    # winning / receiving
    "secures": "win", "secure": "win", "wins": "win", "won": "win", "win": "win",
    "bags": "win", "bag": "win", "receives": "win", "receive": "win",
    "lands": "win", "land": "win", "gets": "win", "obtains": "win",
    "awarded": "win", "awards": "win", "award": "win", "clinches": "win",
    "signs": "sign", "sign": "sign", "signed": "sign", "inks": "sign",
    # losing
    "loses": "lose", "lost": "lose", "loss": "lose", "cancels": "cancel",
    "cancelled": "cancel", "canceled": "cancel", "terminates": "cancel",
    # rising
    "rises": "rise", "rise": "rise", "rose": "rise", "jumps": "rise",
    "jump": "rise", "surges": "rise", "surge": "rise", "climbs": "rise",
    "gains": "rise", "gain": "rise", "soars": "rise", "advances": "rise",
    "increases": "rise", "increase": "rise", "higher": "rise", "grows": "rise",
    "growth": "rise", "expands": "expand", "expansion": "expand",
    # falling
    "falls": "fall", "fall": "fall", "fell": "fall", "drops": "fall",
    "drop": "fall", "slumps": "fall", "slump": "fall", "declines": "fall",
    "decline": "fall", "plunges": "fall", "sinks": "fall", "tumbles": "fall",
    "slides": "fall", "lower": "fall", "collapses": "fall", "collapse": "fall",
    "cuts": "cut", "cut": "cut", "reduces": "cut", "reduce": "cut",
    "lowers": "cut", "slashes": "cut", "trims": "cut",
    "raises": "raise", "raise": "raise", "hikes": "raise", "hike": "raise",
    "boosts": "raise", "lifts": "raise",
    # corporate
    "acquires": "acquire", "acquisition": "acquire", "buys": "acquire",
    "takeover": "acquire", "merger": "merge", "merges": "merge",
    "announces": "announce", "announcement": "announce", "announced": "announce",
    "reports": "report", "reported": "report", "posts": "report",
    "approves": "approve", "approval": "approve", "approved": "approve",
    "launches": "launch", "launch": "launch", "unveils": "launch",
    "orders": "order", "order": "order", "contracts": "contract",
    "contract": "contract", "deal": "contract", "deals": "contract",
    "tender": "tender", "tenders": "tender",
    "profits": "profit", "profit": "profit", "earnings": "earning",
    "revenues": "revenue", "revenue": "revenue", "sales": "sale", "sale": "sale",
    "prices": "price", "price": "price", "pricing": "price",
    "modules": "module", "module": "module", "panels": "module",
    "shares": "share", "stock": "share", "stocks": "share",
    "plants": "plant", "plant": "plant", "factory": "plant",
    "factories": "plant", "facility": "plant", "facilities": "plant",
    "exports": "export", "export": "export", "imports": "import",
    "import": "import", "tariffs": "tariff", "tariff": "tariff",
    "duties": "duty", "duty": "duty", "rates": "rate", "rate": "rate",
    "banks": "bank", "bank": "bank", "loans": "loan", "loan": "loan",
    "deposits": "deposit", "deposit": "deposit",
    # geography
    "usa": "usa", "us": "usa", "america": "usa", "american": "usa",
    "washington": "usa", "uk": "uk", "britain": "uk",
    "chinese": "china", "beijing": "china", "european": "europe", "eu": "europe",
    "brussels": "europe", "indian": "india", "delhi": "india",
    "japanese": "japan", "german": "germany", "russian": "russia",
}

# Words that add emphasis but not identity.
FILLER: Set[str] = {
    "big", "large", "huge", "major", "massive", "significant", "sharp",
    "sharply", "strong", "strongly", "key", "top", "crore", "cr", "lakh",
    "million", "billion", "per", "cent", "percent", "amid", "ahead", "update",
    "report", "reports", "news", "latest", "exclusive", "live", "here", "why",
    "how", "what", "company", "limited", "ltd", "inc", "corp",
}

_MULTIWORD_CANON = (
    ("united states", "usa"),
    ("united kingdom", "uk"),
    ("u s", "usa"),
    ("european union", "europe"),
)


def canonical_tokens(text: str) -> List[str]:
    """Content tokens with verb/geography synonyms folded together."""
    folded = fold(text)
    for phrase, replacement in _MULTIWORD_CANON:
        folded = re.sub(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", replacement, folded)
    out: List[str] = []
    for token in folded.split(" "):
        if not token or token in STOPWORDS or token in FILLER:
            continue
        # Canonicalise before the length filter, or "US" is dropped as noise
        # before it can become "usa".
        token = SYNONYMS.get(token, token)
        if token in FILLER or len(token) <= 2:
            continue
        out.append(token)
    return out


# -- similarity -------------------------------------------------------------


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / len(set_a | set_b)


def title_similarity(a: str, b: str) -> float:
    """Blend of canonical token overlap and sequence similarity.

    Token overlap (after verb/geography canonicalisation) catches re-worded
    headlines about the same event; sequence similarity catches syndicated
    copies with small edits.
    """
    tokens_a, tokens_b = canonical_tokens(a), canonical_tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    set_a, set_b = set(tokens_a), set(tokens_b)
    token_score = jaccard(set_a, set_b)
    # containment: a short headline fully inside a longer one is the same story
    containment = len(set_a & set_b) / min(len(set_a), len(set_b))
    seq_score = SequenceMatcher(None, fold(a), fold(b)).ratio()
    blended = 0.50 * token_score + 0.30 * seq_score + 0.20 * containment
    return round(max(blended, 0.80 * containment), 4)


def numeric_tokens(text: str) -> Set[str]:
    """Numbers in a headline (order sizes, percentages) help separate events."""
    return set(re.findall(r"\d+(?:[.,]\d+)?", fold(text)))


def shingles(text: str, size: int = 4) -> Set[str]:
    words = canonical_tokens(text)
    if len(words) < size:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + size]) for i in range(len(words) - size + 1)}
