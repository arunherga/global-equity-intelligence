"""Investor-relations collection.

The markup in these fixtures mirrors what the real pages served on
2026-09-23: Waaree anchors every filing "Download Now" and puts the only
information in the filename, MSTC uses descriptive anchor text, and several
sites answer a dead investor URL with a normal-looking navigation page.
"""

from __future__ import annotations

import pytest

from src.profiles import load_watchlist
from src.sources.base import CollectionContext, SourceError
from src.sources.company_ir import CompanyIrSource, looks_like_soft_404
from src.sources.ir_titles import (
    best_title,
    is_generic_anchor,
    recognised_word_count,
    segment_words,
    title_from_url,
)

# --- what waaree.com/investor/stock-exchange-disclosure actually serves ----
WAAREE_DISCLOSURES = """
<html><head><title>Stock Exchange Disclosures | Waaree Investor Relations</title></head>
<body>
  <a href="/upload/media/receiptoforder_signed_1790051650.pdf">Download Now</a>
  <a href="/upload/media/outcomeofboardmeeting_signed_1788006177.pdf">Download Now</a>
  <a href="/upload/media/analystmeet_signed_1_1790083676.pdf">Download Now</a>
  <a href="/upload/media/intimationofsearch_ssopl_signed_1787979292.pdf">Download Now</a>
  <a href="/privacy-policy">Privacy Policy</a>
  <a href="https://twitter.com/waaree">Follow us</a>
</body></html>
"""

WAAREE_IR_LANDING = """
<html><head><title>Investor Relations | Waaree Energies Limited</title></head>
<body>
  <a href="/investor/stock-exchange-disclosure">Stock Exchange Disclosure</a>
  <a href="/investor/corporate-governance">Corporate Governance</a>
  <a href="/careers">Careers</a>
</body></html>
"""

# --- MSTC labels its links properly --------------------------------------
MSTC_ANNOUNCEMENTS = """
<html><head><title>Corporate Announcements | MSTC Limited</title></head>
<body>
  <a href="/files/Vigilance_TII.pdf">MOU between MSTC LIMITED and TRANSPARENCY INTERNATIONAL INDIA</a>
  <a href="/files/Credit%20Rating.pdf">Credit Rating</a>
  <a href="/files/BOARDDIVERSITYPOLICY.pdf">Board Diversity Policy</a>
  <a href="/contact">Contact us</a>
</body></html>
"""

# --- a soft 404: 200 OK, full navigation, no content ----------------------
KSOLVES_SOFT_404 = """
<html><head><title>Page not found - Ksolves</title></head>
<body><a href="/about">About us</a><a href="/careers">Careers</a></body></html>
"""

KSOLVES_HOME = """
<html><head><title>Ksolves India Limited</title></head>
<body><a href="/investors">Investor Relations</a><a href="/blog">Blog</a></body></html>
"""

KSOLVES_IR = """
<html><head><title>Investors | Ksolves</title></head>
<body>
  <a href="/docs/outcomeofboardmeeting_1770000000.pdf">Download</a>
  <a href="/docs/shareholding_pattern_q1.pdf">Download</a>
</body></html>
"""


class FakeResponse:
    def __init__(self, text: str, url: str = ""):
        self.text = text
        self.content = text.encode()
        self.url = url


class FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        for needle, page in self.pages.items():
            if needle in url:
                return FakeResponse(page, url)
        raise SourceError(f"404 for {url}")

    def sleep(self):
        pass


@pytest.fixture(scope="module")
def profiles():
    return load_watchlist()


def collect(profile, pages):
    client = FakeClient(pages)
    source = CompanyIrSource({}, client)
    articles = source.fetch(CollectionContext(profiles=[profile], queries={}))
    return source, articles


# -- title recovery ---------------------------------------------------------


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.test/receiptoforder_signed_1790051650.pdf", "Receipt of order"),
        ("https://x.test/outcomeofboardmeeting_signed_1788006177.pdf", "Outcome of board meeting"),
        ("https://x.test/analystmeet_signed_1_1790083676.pdf", "Analyst meet"),
        ("https://x.test/investormeet_signed_1789563230.pdf", "Investor meet"),
        ("https://x.test/corporate_governance_report_q3_new_1_1758706273.pdf",
         "Corporate governance report q3"),
        ("https://x.test/Credit%20Rating.pdf", "Credit Rating"),
    ],
)
def test_titles_are_recovered_from_filenames(url, expected):
    assert title_from_url(url) == expected


def test_a_word_is_never_mangled_by_a_partial_split():
    """Regression: "corporate" was once split into "corpor at e"."""
    assert segment_words("corporate") == ["corporate"]
    assert segment_words("receiptoforder") == ["receipt", "of", "order"]
    # an unknown token survives intact rather than becoming confetti
    assert segment_words("ssopl") == ["ssopl"]


def test_digits_and_timestamps_are_dropped():
    assert title_from_url("https://x.test/16-09-2026a-wn%20.pdf").isascii()
    assert "1790051650" not in title_from_url("https://x.test/order_signed_1790051650.pdf")


def test_generic_anchors_are_recognised():
    for text in ("Download", "Download Now", "Click here", "Read more", "View", ""):
        assert is_generic_anchor(text)
    assert not is_generic_anchor("Outcome of Board Meeting held on 12 August")


def test_descriptive_anchor_text_wins_over_the_filename():
    assert best_title(
        "MOU between MSTC LIMITED and TRANSPARENCY INTERNATIONAL INDIA",
        "https://x.test/Vigilance_TII.pdf",
    ).startswith("MOU between")


def test_filename_is_used_when_the_anchor_says_nothing():
    assert best_title("Download Now", "https://x.test/receiptoforder_signed_1.pdf") == (
        "Receipt of order"
    )


def test_recognised_words_are_counted():
    assert recognised_word_count("Outcome of board meeting") >= 2
    assert recognised_word_count("Lorem ipsum dolor") == 0


# -- soft 404 detection -----------------------------------------------------


def test_a_not_found_page_is_detected_despite_a_200():
    assert looks_like_soft_404(KSOLVES_SOFT_404)
    assert looks_like_soft_404("<html><title>404 Not Found</title></html>")
    assert looks_like_soft_404("", "https://mstcindia.co.in/404.html?aspxerrorpath=/Investors.aspx")


def test_a_real_page_is_not_mistaken_for_a_404():
    assert not looks_like_soft_404(WAAREE_DISCLOSURES)
    assert not looks_like_soft_404(MSTC_ANNOUNCEMENTS)


# -- collection -------------------------------------------------------------


def test_filings_are_found_when_every_anchor_says_download_now(profiles):
    """The Waaree case: the filename is the only information on the page."""
    profile = profiles.get("WAAREEENER")
    source, articles = collect(profile, {"stock-exchange-disclosure": WAAREE_DISCLOSURES})
    titles = [a.title for a in articles]

    assert any("Receipt of order" in t for t in titles)
    assert any("Outcome of board meeting" in t for t in titles)
    assert all(a.is_official for a in articles)
    assert all(a.tickers_hint == ["WAAREEENER"] for a in articles)
    # navigation and social links are not filings
    assert not any("Privacy" in t or "Follow" in t for t in titles)


def test_descriptive_anchors_are_used_as_they_are(profiles):
    profile = profiles.get("MSTCLTD")
    _, articles = collect(profile, {"CorpAnnouncement": MSTC_ANNOUNCEMENTS})
    titles = " | ".join(a.title for a in articles)
    assert "TRANSPARENCY INTERNATIONAL" in titles
    assert "Credit Rating" in titles
    assert "Contact us" not in titles


def test_a_soft_404_is_reported_as_a_failure_not_an_empty_success(profiles):
    """Regression: these used to be logged as 'reachable but nothing parseable'."""
    profile = profiles.get("KSOLVES")
    source, articles = collect(profile, {"ksolves.com": KSOLVES_SOFT_404})
    assert articles == []
    assert any("not-found page" in e for e in source.errors)


def test_a_dead_investor_page_falls_back_to_the_site_root(profiles):
    """Discovery: find the investor section rather than trusting a stale URL."""
    profile = profiles.get("KSOLVES")
    profile.ir["investor_page"] = "https://www.ksolves.com/investor-relations"
    try:
        source, articles = collect(profile, {
            "investor-relations": KSOLVES_SOFT_404,
            "ksolves.com/investors": KSOLVES_IR,
            "https://www.ksolves.com/": KSOLVES_HOME,
        })
        titles = " | ".join(a.title for a in articles)
        assert "Outcome of board meeting" in titles
        assert any("falling back" in n for n in source.notes)
    finally:
        profile.ir.pop("investor_page", None)


def test_announcement_subpages_are_followed(profiles):
    profile = profiles.get("WAAREEENER")
    profile.ir["investor_page"] = "https://www.waaree.com/investor/"
    try:
        _, articles = collect(profile, {
            "/investor/stock-exchange-disclosure": WAAREE_DISCLOSURES,
            "/investor/": WAAREE_IR_LANDING,
        })
        assert any("Receipt of order" in a.title for a in articles)
    finally:
        profile.ir["investor_page"] = (
            "https://www.waaree.com/investor/stock-exchange-disclosure"
        )


def test_a_company_with_no_ir_site_is_skipped_quietly(profiles):
    profile = profiles.get("FRESHARA")
    source, articles = collect(profile, {})
    assert articles == []
    assert any("no IR page configured" in n for n in source.notes)


def test_a_dead_host_is_recorded_and_survived(profiles):
    profile = profiles.get("RAVEL")
    source, articles = collect(profile, {})
    assert articles == []
    assert source.errors


def test_duplicate_links_are_collapsed(profiles):
    profile = profiles.get("MSTCLTD")
    doubled = MSTC_ANNOUNCEMENTS + MSTC_ANNOUNCEMENTS
    _, articles = collect(profile, {"CorpAnnouncement": doubled})
    assert len({a.url for a in articles}) == len(articles)
