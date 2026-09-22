"""Source plumbing and, above all, failure isolation."""

from __future__ import annotations

import json

import pytest

from src.config import FeedConfig, load_config
from src.models import SourceType
from src.query_generator import Query, QueryKind
from src.sources import build_sources
from src.sources.base import (
    CollectionContext,
    HttpClient,
    Source,
    SourceError,
    extract_links,
    find_feed_links,
    parse_feed_entries,
    site_root,
)
from src.sources.gdelt import GdeltSource
from src.sources.google_news import GoogleNewsSource
from src.sources.rss import RssSource

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
  <title>Waaree bags a 1 GW order - The Example Times</title>
  <link>https://pub.test/story/1</link>
  <pubDate>Mon, 22 Sep 2026 06:00:00 GMT</pubDate>
  <description>Order for modules.</description>
  <source url="https://pub.test">The Example Times</source>
</item>
<item>
  <title>Coal India output rises</title>
  <link>https://pub.test/story/2</link>
  <pubDate>Mon, 22 Sep 2026 05:00:00 GMT</pubDate>
</item>
</channel></rss>"""


class FakeResponse:
    def __init__(self, content=b"", text="", payload=None, url=""):
        self.content = content
        self.text = text or content.decode("utf-8", "ignore")
        self._payload = payload
        self.url = url

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeClient:
    """An HttpClient stand-in. Tests never touch the network."""

    def __init__(self, responses=None, fail_with=None):
        self.responses = responses or {}
        self.fail_with = fail_with
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append(url)
        if self.fail_with:
            raise self.fail_with
        for needle, response in self.responses.items():
            if needle in url:
                return response
        raise SourceError(f"no stub for {url}")

    def sleep(self):
        pass


def context(profiles=()):
    return CollectionContext(
        profiles=list(profiles),
        queries={"WAAREEENER": [Query(text="Waaree Energies", ticker="WAAREEENER",
                                      kind=QueryKind.COMPANY)]},
        since_days=2,
        max_articles_per_query=5,
    )


# -- parsing ---------------------------------------------------------------


def test_feed_parsing_extracts_the_real_publisher():
    articles = list(parse_feed_entries(RSS, "Google News", "google_news"))
    assert len(articles) == 2
    assert articles[0].source_name == "The Example Times"
    assert articles[0].published is not None


def test_items_without_a_link_or_title_are_skipped():
    broken = b'<?xml version="1.0"?><rss><channel><item><title>No link</title></item></channel></rss>'
    assert list(parse_feed_entries(broken, "x", "y")) == []


def test_feed_discovery_from_a_page_head():
    html = '<link rel="alternate" type="application/rss+xml" href="/feed">'
    assert find_feed_links(html, "https://site.test/page") == ["https://site.test/feed"]


def test_link_extraction_from_a_listing_page():
    links = extract_links('<a href="/n/1">Coal auction notice</a>', "https://coal.test/")
    assert links == [("https://coal.test/n/1", "Coal auction notice")]


def test_site_root():
    assert site_root("https://a.test/b/c?d=1") == "https://a.test/"
    assert site_root("nonsense") == ""


# -- google news -----------------------------------------------------------


def test_google_news_builds_a_windowed_query():
    source = GoogleNewsSource({}, FakeClient())
    url = source.build_url(Query(text='"Waaree Energies"', ticker="WAAREEENER",
                                 kind=QueryKind.COMPANY), since_days=2)
    assert "when%3A2d" in url
    assert "hl=en-IN" in url


def test_google_news_backfill_uses_a_date_range():
    source = GoogleNewsSource({}, FakeClient())
    url = source.build_url(Query(text="x", ticker="T", kind=QueryKind.COMPANY),
                           since_days=30, until_days=16)
    assert "after%3A" in url and "before%3A" in url


def test_google_news_collects_and_tags():
    client = FakeClient({"news.google.com": FakeResponse(content=RSS)})
    source = GoogleNewsSource({}, client)
    articles = source.fetch(context())
    assert len(articles) == 2
    assert articles[0].raw["query_ticker"] == "WAAREEENER"
    assert articles[0].collector == "google_news"


# -- gdelt -----------------------------------------------------------------


def test_gdelt_only_runs_international_queries():
    client = FakeClient({"gdelt": FakeResponse(payload={"articles": []})})
    source = GdeltSource({"international_only": True}, client)
    source.fetch(context())
    assert client.calls == [], "a domestic query must not be sent to GDELT"


def test_gdelt_parses_articles():
    payload = {"articles": [{"url": "https://x.test/a", "title": "China solar prices fall",
                             "domain": "x.test", "seendate": "20260922T060000Z"}]}
    client = FakeClient({"gdelt": FakeResponse(payload=payload)})
    source = GdeltSource({}, client)
    ctx = context()
    ctx.queries = {"W": [Query(text="China solar module prices", ticker="WAAREEENER",
                               kind=QueryKind.INTERNATIONAL, international=True)]}
    articles = source.fetch(ctx)
    assert len(articles) == 1
    assert articles[0].source_domain == "x.test"


def test_gdelt_survives_an_html_error_page():
    client = FakeClient({"gdelt": FakeResponse(text="<html>overloaded</html>")})
    source = GdeltSource({}, client)
    ctx = context()
    ctx.queries = {"W": [Query(text="x", ticker="WAAREEENER", kind=QueryKind.INTERNATIONAL,
                               international=True)]}
    assert source.fetch(ctx) == []
    assert any("non-JSON" in e for e in source.errors)


# -- failure isolation -----------------------------------------------------


def test_a_dead_source_returns_nothing_and_records_the_error():
    source = GoogleNewsSource({}, FakeClient(fail_with=SourceError("403 Forbidden")))
    assert source.fetch(context()) == []
    assert source.errors
    assert not source.outcome(0, 0.1).ok


def test_one_broken_feed_does_not_stop_the_others():
    feeds = [
        FeedConfig(name="Broken", url="https://broken.test/feed"),
        FeedConfig(name="Working", url="https://working.test/feed"),
    ]
    client = FakeClient({"working.test/feed": FakeResponse(content=RSS)})
    source = RssSource({}, client, feeds=feeds)
    articles = source.fetch(context())
    assert len(articles) == 2, "the working feed must still be collected"
    assert any("Broken" in e for e in source.errors)


def test_a_moved_feed_is_rediscovered():
    """Publishers move feeds and leave the old path 404ing."""

    pages = {
        "https://old.test/": FakeResponse(
            text='<link rel="alternate" type="application/rss+xml" href="/rss/new">'
        ),
        "https://old.test/rss/new": FakeResponse(content=RSS),
    }

    class MovedFeedClient(FakeClient):
        def get(self, url, **kwargs):
            self.calls.append(url)
            if url == "https://old.test/feed":
                raise SourceError("404 Not Found")
            if url in pages:
                return pages[url]
            raise SourceError(f"no stub for {url}")

    client = MovedFeedClient()
    source = RssSource({}, client, feeds=[FeedConfig(name="Moved", url="https://old.test/feed")])
    articles = source.fetch(context())
    assert len(articles) == 2
    assert any("moved" in n for n in source.notes)


def test_outcome_reports_what_actually_happened():
    source = GoogleNewsSource({}, FakeClient({"news.google.com": FakeResponse(content=RSS)}))
    articles = source.fetch(context())
    outcome = source.outcome(len(articles), 1.25)
    assert outcome.ok
    assert outcome.articles == 2
    assert outcome.attempted == 1
    assert outcome.duration_s == 1.25


# -- registry ---------------------------------------------------------------


def test_only_enabled_sources_are_built():
    config = load_config(overrides={"sources": {"gdelt": {"enabled": False}}})
    names = [s.name for s in build_sources(config, HttpClient(), ["WAAREEENER"])]
    assert "gdelt" not in names
    assert "google_news" in names


def test_official_sources_are_polled_first():
    config = load_config()
    names = [s.name for s in build_sources(config, HttpClient(), ["WAAREEENER"])]
    assert names.index("nse") < names.index("google_news")


# -- circuit breaker --------------------------------------------------------


def test_a_blocked_host_is_abandoned_after_repeated_failures():
    """A totally blocked host must not cost a full run of timeouts."""
    client = FakeClient(fail_with=SourceError("403 Forbidden"))
    source = GoogleNewsSource({}, client)
    ctx = context()
    ctx.queries = {
        "W": [
            Query(text=f"query {i}", ticker="WAAREEENER", kind=QueryKind.COMPANY)
            for i in range(40)
        ]
    }
    source.fetch(ctx)
    assert source.gave_up
    assert len(client.calls) <= source.GIVE_UP_AFTER
    assert any("circuit breaker" in n for n in source.notes)


def test_a_success_resets_the_breaker():
    source = GoogleNewsSource({}, FakeClient())
    source.consecutive_failures = 3
    source.note_success()
    assert source.consecutive_failures == 0
    assert not source.should_give_up()


def test_error_list_stays_readable():
    source = GoogleNewsSource({}, FakeClient())
    for i in range(50):
        source.record_error(f"query {i}", "403")
    assert len(source.errors) <= 6
