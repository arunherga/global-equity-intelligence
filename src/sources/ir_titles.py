"""Deriving a usable title for a filing link.

Investor-relations and government pages overwhelmingly label their links
"Download", "Download Now" or "Click here". The live run of 2026-09-22
reported several IR pages as "reachable but nothing parseable" for exactly
this reason: the anchor text carries no information at all.

The information is in the filename. Waaree's stock-exchange disclosures, for
example, are all anchored "Download Now" and named:

    receiptoforder_signed_1790051650.pdf
    outcomeofboardmeeting_signed_1788006177.pdf
    intimationofsearch_ssopl_signed_1787979292.pdf

So a title is recovered from the URL: strip the extension, the upload
timestamp and the signing suffix, then split the run-together words against a
vocabulary of the terms that actually appear in Indian exchange filings.
"""

from __future__ import annotations

import os
import re
from typing import Iterable, List, Optional
from urllib.parse import unquote, urlsplit

# Anchor text that says nothing about the document behind it.
GENERIC_ANCHORS = frozenset({
    "download", "download now", "download pdf", "click here", "click here to view",
    "click here to view details", "read more", "view", "view more", "view all",
    "view details", "more", "know more", "pdf", "link", "here", "details",
    "open", "open link", "see more", "learn more", "continue reading", "->", "»",
})

# Words that actually appear in Indian exchange filings, used to split
# run-together filenames like "outcomeofboardmeeting".
FILING_WORDS = frozenset("""
acquisition addendum agm allotment analyst annual appointment audit audited
agreement articles association charter corporate corporation conference
convertible debenture equity exchange grievance integrity memorandum mou
undertaking vigilance
gazette incorporation issue listing material merger monitoring newspaper
nomination proceeds publication record reconciliation remuneration
resolution revision scheme scrutiny share shares stock submission utilisation
auditor award ballot board brsr buyback capacity capex ceo certificate cfo
chairman closure code commissioning company compliance conduct contract cost
credit director disclosure disclosures dividend draft egm esg expansion
extraordinary filing final financial fund general governance half insider
integrated intimation investor investors letter loa meet meeting meetings
merger newspaper notice notices order orders outcome party pattern plant
policy postal presentation press quarterly raising rating receipt related
release report reports resignation result results scrutinizer search
secretarial secretary shareholding signed statement statements subsidiary
sustainability transcript trading transfer unaudited update updates voting
window yearly
""".split())

# Short connectors, kept separate so they are only used to join real words.
CONNECTORS = frozenset({"of", "for", "on", "and", "to", "in", "at", "the", "from", "with", "under", "as", "by"})

_VOCAB = FILING_WORDS | CONNECTORS

# Trailing noise: upload timestamps, "signed", revision counters.
_TIMESTAMP = re.compile(r"[_-]\d{9,}$")
_TRAILING_NOISE = re.compile(r"[_-](signed|final|new|copy|v\d+|\d{1,3})$", re.IGNORECASE)
_SEPARATORS = re.compile(r"(?:%20|[_\-+\s])+")
_WS = re.compile(r"\s+")

MIN_DESCRIPTIVE_ANCHOR = 18


def is_generic_anchor(text: str) -> bool:
    """Is this anchor text boilerplate rather than a description?"""
    cleaned = _WS.sub(" ", (text or "")).strip().lower().strip(".:-–—>»")
    if not cleaned:
        return True
    return cleaned in GENERIC_ANCHORS or len(cleaned) < 4


def segment_words(token: str) -> List[str]:
    """Split a run-together lowercase token using the filing vocabulary.

    Greedy longest-match from the left. A stretch that matches nothing is kept
    verbatim rather than chopped into noise.
    """
    token = token.lower()
    if not token or token in _VOCAB:
        return [token] if token else []

    out: List[str] = []
    unknown = ""
    index = 0
    while index < len(token):
        match: Optional[str] = None
        # longest first, so "meetings" wins over "meeting"
        for end in range(len(token), index, -1):
            candidate = token[index:end]
            if len(candidate) >= 2 and candidate in _VOCAB:
                match = candidate
                break
        if match:
            if unknown:
                out.append(unknown)
                unknown = ""
            out.append(match)
            index += len(match)
        else:
            unknown += token[index]
            index += 1
    if unknown:
        out.append(unknown)

    # Only trust a split where every piece is a real word. A partial match
    # turns "corporate" into "corpor at e", which is worse than not splitting.
    if any(part not in _VOCAB for part in out):
        return [token]
    return out


def title_from_url(url: str) -> str:
    """Recover a human title from a document URL.

    >>> title_from_url("https://x.test/upload/receiptoforder_signed_1790051650.pdf")
    'Receipt of order'
    """
    if not url:
        return ""
    path = urlsplit(url).path
    name = unquote(os.path.basename(path))
    if not name:
        return ""
    stem, _ = os.path.splitext(name)

    stem = _TIMESTAMP.sub("", stem)
    for _ in range(3):                      # "..._signed_1" -> "..."
        new = _TRAILING_NOISE.sub("", stem)
        if new == stem:
            break
        stem = new

    words: List[str] = []
    for chunk in _SEPARATORS.split(stem):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.isdigit():          # dates and counters carry no meaning here
            continue
        if any(c.isupper() for c in chunk) and any(c.islower() for c in chunk):
            # CamelCase filenames already carry their own boundaries.
            words.extend(re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", chunk))
        else:
            words.extend(segment_words(chunk))

    text = _WS.sub(" ", " ".join(w for w in words if w)).strip()
    if not text:
        return ""
    return text[0].upper() + text[1:]


def best_title(anchor_text: str, url: str, fallback: str = "") -> str:
    """Prefer descriptive anchor text; otherwise recover it from the URL."""
    cleaned = _WS.sub(" ", (anchor_text or "")).strip()
    if not is_generic_anchor(cleaned) and len(cleaned) >= MIN_DESCRIPTIVE_ANCHOR:
        return cleaned
    from_url = title_from_url(url)
    if from_url and len(from_url) >= 6:
        return from_url
    return cleaned or fallback


def recognised_word_count(title: str) -> int:
    """How many words of the title are known filing vocabulary."""
    return sum(1 for w in re.findall(r"[a-z]+", (title or "").lower()) if w in FILING_WORDS)
