"""Aggregator link resolution.

Every rule here exists because a previous version of this code shipped without
it: one release resolved 400 links to a 16-pixel favicon, the next resolved
nothing at all and said nothing about why.
"""

from __future__ import annotations

import pytest

from src.models import Article
from src.resolve import (
    describe_page,
    looks_like_article,
    needs_resolution,
    resolve_article_urls,
    resolve_one,
)


class FakeResponse:
    def __init__(self, text="", url=""):
        self.text = text
        self.url = url


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.response

    def sleep(self):
        pass


def test_aggregator_links_are_detected():
    assert needs_resolution("https://news.google.com/rss/articles/CBMi")
    assert not needs_resolution("https://www.reuters.com/markets/story")


@pytest.mark.parametrize(
    "url",
    [
        "https://lh3.googleusercontent.com/x=w16",     # the famous favicon
        "https://www.google.com/",
        "https://pub.test/logo.png",
        "https://pub.test/",
        "https://twitter.com/share",
        "not-a-url",
    ],
)
def test_non_articles_are_rejected(url):
    assert not looks_like_article(url)


def test_real_article_urls_are_accepted():
    assert looks_like_article("https://pub.test/markets/waaree-wins-order-12345")


def test_resolution_from_the_redirect_body():
    html = '<a href="https://pub.test/markets/waaree-wins-order-12345">read</a>'
    client = FakeClient(FakeResponse(text=html, url="https://news.google.com/x"))
    resolved = resolve_one("https://news.google.com/rss/articles/CBMi", client)
    assert resolved == "https://pub.test/markets/waaree-wins-order-12345"


def test_an_unresolvable_link_is_left_alone():
    client = FakeClient(FakeResponse(text="<html>nothing useful</html>",
                                     url="https://news.google.com/x"))
    original = "https://news.google.com/rss/articles/CBMi"
    assert resolve_one(original, client) == original


def test_a_request_failure_is_survivable():
    client = FakeClient(error=RuntimeError("timeout"))
    notes = []
    original = "https://news.google.com/rss/articles/CBMi"
    assert resolve_one(original, client, notes) == original
    assert notes and "request failed" in notes[0]


def test_failures_are_described_for_the_next_fix():
    description = describe_page('<c-wiz jslog="1">enable JavaScript</c-wiz>',
                                "https://news.google.com/x")
    assert "news.google.com" in description
    assert "c-wiz" in description or "needs-js" in description


def test_repeated_targets_are_ignored():
    """Two links resolving to the same page means the parse latched onto furniture."""
    html = '<a href="https://pub.test/markets/some-shared-masthead-link">x</a>'
    client = FakeClient(FakeResponse(text=html, url="https://news.google.com/x"))
    articles = [
        Article(title=f"story {i}", url=f"https://news.google.com/rss/articles/{i}")
        for i in range(3)
    ]
    resolved, count, attempted, _ = resolve_article_urls(articles, client, limit=10)
    assert count == 1, "only the first article may take the shared target"
    assert attempted == 3


def test_the_budget_is_respected():
    client = FakeClient(FakeResponse(text="", url="https://news.google.com/x"))
    articles = [
        Article(title=f"story {i}", url=f"https://news.google.com/rss/articles/{i}")
        for i in range(50)
    ]
    resolve_article_urls(articles, client, limit=5)
    assert client.calls == 5


def test_resolution_gives_up_after_repeated_failures():
    client = FakeClient(FakeResponse(text="<html></html>", url="https://news.google.com/x"))
    articles = [
        Article(title=f"story {i}", url=f"https://news.google.com/rss/articles/{i}")
        for i in range(60)
    ]
    _, _, attempted, diagnostics = resolve_article_urls(articles, client, limit=60)
    assert attempted == 20
    assert any("gave up" in d for d in diagnostics)


def test_direct_links_are_left_untouched():
    client = FakeClient(FakeResponse())
    articles = [Article(title="x", url="https://www.reuters.com/markets/story-1")]
    resolve_article_urls(articles, client, limit=10)
    assert client.calls == 0
