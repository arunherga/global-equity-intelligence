"""Word-boundary matching primitives.

The sibling project shipped substring matching once and learned that "pp"
matches approves, "ps" matches tops and "pet" matches competition. Those exact
classes of failure are regression tests here.
"""

from __future__ import annotations

import pytest

from src.matching import (
    canonical_tokens,
    contains_any,
    contains_phrase,
    covered_by,
    find_spans,
    fold,
    matched_terms,
    title_similarity,
)


@pytest.mark.parametrize(
    "needle,haystack",
    [
        ("api", "The company raised capital for expansion"),      # capital
        ("pet", "competition in the sector intensified"),          # competition
        ("cut", "the executive team met on Monday"),               # executive
        ("ban", "urban demand recovered sharply"),                 # urban
        ("ofs", "profits rose in the quarter"),                    # profits
        ("coal", "charcoal prices eased"),                         # charcoal
        ("tmb", "the atmbank merger closed"),                      # atmbank
    ],
)
def test_substring_matches_are_rejected(needle, haystack):
    assert not contains_phrase(fold(haystack), needle)


@pytest.mark.parametrize(
    "needle,haystack",
    [
        ("api", "API prices surged this quarter"),
        ("coal", "Coal output rose"),
        ("solar module", "Solar modules shipped to the US"),
        ("solar modules", "One solar module costs less now"),
        ("repo rate", "the repo-rate decision is due"),
        ("usd/inr", "USD/INR closed weaker"),
    ],
)
def test_real_matches_are_found(needle, haystack):
    assert contains_phrase(fold(haystack), needle)


def test_plural_tolerance_runs_both_ways():
    assert contains_phrase(fold("module prices fell"), "module price")
    assert contains_phrase(fold("the module price fell"), "module prices")


def test_words_ending_in_ss_are_not_singularised():
    assert contains_phrase(fold("business demand improved"), "business")
    assert not contains_phrase(fold("busines"), "business")


def test_accents_and_curly_quotes_fold():
    assert contains_phrase(fold("L'Oréal reported results"), "L'Oreal")
    assert contains_phrase(fold("jalapeños shipped"), "jalapenos")


def test_covered_by_detects_a_blocking_phrase():
    text = fold("Ravel Electronics wins an order")
    span = find_spans(text, "ravel")[0]
    assert covered_by(span, text, ["ravel electronics"]) == "ravel electronics"


def test_covered_by_allows_a_standalone_mention():
    text = fold("Ravel expands its haircare range")
    span = find_spans(text, "ravel")[0]
    assert covered_by(span, text, ["ravel electronics"]) is None


def test_matched_terms_prefers_the_most_specific():
    text = fold("solar module prices fell in China")
    assert matched_terms(text, ["solar", "solar module prices"])[0] == "solar module prices"


def test_canonical_tokens_fold_verbs_and_geography():
    tokens = canonical_tokens("Waaree secures large US module order")
    assert "win" in tokens          # secures -> win
    assert "usa" in tokens          # us -> usa
    assert "large" not in tokens    # filler dropped


def test_title_similarity_groups_rewordings_and_separates_topics():
    same = title_similarity(
        "Newcastle thermal coal prices slump",
        "Thermal coal prices at Newcastle fall sharply",
    )
    different = title_similarity("Coal India output rises", "RBI cuts repo rate")
    assert same > 0.7
    assert different < 0.2
    assert same > different


def test_contains_any_short_circuits_cleanly():
    assert contains_any(fold("USFDA issues a warning letter"), ["form 483", "warning letter"])
    assert not contains_any(fold("routine inspection completed"), ["warning letter"])
