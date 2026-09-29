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


# -- multi-locale Google News ---------------------------------------------
#
# Google News is localised: the Spanish edition carries the Navarrese press
# on Aceitunas Sarasa that the Indian edition never shows, and the Gulf and
# African editions carry the equipment trade press on HexL.


def _news_source(**config):
    from src.config import load_config
    from src.sources.base import HttpClient
    from src.sources.google_news import GoogleNewsSource

    return GoogleNewsSource(config, HttpClient.from_config(load_config().http))


def test_locale_codes_become_googles_own_parameters():
    source = _news_source()

    assert source.locale_parameters("es-ES") == ("es", "ES", "ES:es")
    assert source.locale_parameters("en-ZA") == ("en", "ZA", "ZA:en")
    assert source.locale_parameters("en_AE") == ("en", "AE", "AE:en")


def test_an_unparseable_locale_falls_back_rather_than_building_a_bad_url():
    """A typo must cost one locale, not every query for that ticker."""
    source = _news_source()
    default = source.locale_parameters("")

    for junk in ("rubbish", "es-", "-ES", "es-ES-extra", ""):
        assert source.locale_parameters(junk) == default, junk


def test_a_foreign_locale_reaches_the_url():
    from src.models import Relationship
    from src.query_generator import Query, QueryKind

    source = _news_source()
    query = Query(text='"Aceitunas Sarasa"', ticker="FRESHARA", kind=QueryKind.COMPANY)

    url = source.build_url(query, since_days=2, locale="es-ES")

    assert "hl=es" in url and "gl=ES" in url and "ceid=ES:es" in url
    assert "Aceitunas" in url


def test_only_company_like_queries_are_repeated_abroad():
    """Repeating a macro query in six editions buys six copies of one wire."""
    from src.query_generator import Query, QueryKind

    source = _news_source(max_locale_queries_per_ticker=10)
    queries = [
        Query(text='"Jinkushal"', ticker="JKIPL", kind=QueryKind.COMPANY),
        Query(text="India coal imports", ticker="JKIPL", kind=QueryKind.MACRO),
        Query(text="steel prices", ticker="JKIPL", kind=QueryKind.COMMODITY),
    ]
    spent = {}

    pairs = list(source._with_locales(queries, {"JKIPL": ["en-ZA", "en-AE"]}, 10, spent))

    home = [q for q, loc in pairs if not loc]
    abroad = [(q.kind.value, loc) for q, loc in pairs if loc]
    assert len(home) == 3, "every query still runs at home"
    assert abroad == [("COMPANY", "en-ZA"), ("COMPANY", "en-AE")]


def test_the_per_ticker_locale_budget_is_enforced():
    from src.query_generator import Query, QueryKind

    source = _news_source()
    queries = [
        Query(text=f'"brand {n}"', ticker="JKIPL", kind=QueryKind.COMPANY)
        for n in range(5)
    ]
    spent = {}

    pairs = list(
        source._with_locales(queries, {"JKIPL": ["en-ZA", "en-NG", "en-AE"]}, 3, spent)
    )

    assert sum(1 for _, loc in pairs if loc) == 3, "capped, not five times three"
    assert spent == {"JKIPL": 3}


def test_a_company_with_no_locales_costs_nothing_extra():
    from src.query_generator import Query, QueryKind

    source = _news_source()
    queries = [Query(text='"Ravelcare"', ticker="RAVEL", kind=QueryKind.COMPANY)]
    spent = {}

    pairs = list(source._with_locales(queries, {"RAVEL": []}, 3, spent))

    assert [loc for _, loc in pairs] == [""]
    assert spent == {}


def test_the_watchlist_locales_are_well_formed(watchlist):
    """A malformed code silently degrades to the Indian edition."""
    import re

    pattern = re.compile(r"^[a-z]{2}-[A-Z]{2}$")
    declared = 0
    for profile in watchlist:
        for locale in profile.news_locales:
            assert pattern.match(locale), f"{profile.ticker}: {locale!r}"
            declared += 1
    assert declared, "at least one company should declare a foreign edition"


# -- Reddit, as the legitimate stand-in for marketplace reviews -----------


def _reddit(**config):
    from src.sources.base import HttpClient
    from src.sources.reddit import RedditSource

    settings = {"subreddits": ["IndianSkincareAddicts"], "min_score": 2}
    settings.update(config)
    return RedditSource(settings, HttpClient())


def _post(**overrides):
    post = {
        "title": "Tried the Ravel PRO zero hairfall shampoo for 6 weeks",
        "permalink": "/r/IndianHaircare/comments/abc/tried_ravel_pro/",
        "selftext": "Shedding is noticeably down but the smell is strong.",
        "score": 42,
        "num_comments": 17,
        "subreddit": "IndianHaircare",
        "created_utc": 1790000000.0,
    }
    post.update(overrides)
    return {"data": post}


def _listing(*posts):
    return {"data": {"children": list(posts)}}


def test_reddit_parses_a_listing_into_articles():
    articles = _reddit().parse(_listing(_post()), ticker="RAVEL", term="Ravel PRO")

    assert len(articles) == 1
    article = articles[0]
    assert article.source_name == "r/IndianHaircare"
    assert article.source_domain == "reddit.com"
    assert article.url.startswith("https://www.reddit.com/r/IndianHaircare/")
    assert article.tickers_hint == ["RAVEL"]
    assert article.published is not None and article.published.year > 2020


def test_consumer_items_are_marked_and_scored_as_social_media():
    """They must never be mistakable for a filing."""
    from src.models import SourceType

    article = _reddit().parse(_listing(_post()), ticker="RAVEL", term="x")[0]

    assert article.source_type == SourceType.SOCIAL_MEDIA
    assert article.raw["consumer"] is True
    assert article.raw["score"] == 42
    assert article.raw["num_comments"] == 17
    assert not article.is_official


def test_posts_nobody_engaged_with_are_dropped():
    """One person with an opinion is not a signal."""
    listing = _listing(_post(score=0), _post(score=1), _post(score=9))

    articles = _reddit(min_score=2).parse(listing, ticker="RAVEL", term="x")

    assert [a.raw["score"] for a in articles] == [9]


def test_nsfw_and_removed_posts_are_dropped():
    listing = _listing(
        _post(over_18=True),
        _post(removed_by_category="moderator"),
        _post(title="A real review"),
    )

    articles = _reddit().parse(listing, ticker="RAVEL", term="x")

    assert [a.title for a in articles] == ["A real review"]


def test_a_post_with_no_title_or_no_url_is_skipped():
    listing = _listing(_post(title=""), _post(permalink="", url=""), _post())

    assert len(_reddit().parse(listing, ticker="RAVEL", term="x")) == 1


def test_a_malformed_payload_returns_nothing_rather_than_raising():
    """A source must never take a run down; the collector treats a raise as
    a hard failure of that source."""
    source = _reddit()

    for payload in ({}, {"data": None}, {"data": {"children": None}},
                    {"data": {"children": [None, {}, {"data": None}]}}):
        assert source.parse(payload, ticker="RAVEL", term="x") == []


def test_search_urls_quote_multi_word_terms():
    source = _reddit()

    scoped = source.build_url("Ravel PRO", "IndianHaircare")
    sitewide = source.build_url("HexL")

    assert "/r/IndianHaircare/search.json" in scoped
    assert "restrict_sr=1" in scoped
    assert "%22Ravel+PRO%22" in scoped, "a phrase must be quoted"
    assert "/r/" not in sitewide and "search.json" in sitewide


def test_only_companies_that_opted_in_are_searched(watchlist):
    from src.sources.base import CollectionContext

    context = CollectionContext(
        profiles=list(watchlist), queries={}, since_days=2,
        max_articles_per_query=10, dry_run=True,
    )

    pairs = list(_reddit().terms_for(context))
    tickers = {t for t, _ in pairs}

    assert tickers == {"RAVEL", "JKIPL", "FRESHARA"}
    assert ("RAVEL", "Ravel PRO") in pairs
    assert all(term.strip() for _, term in pairs)


def test_the_reddit_user_agent_identifies_the_project():
    """A generic UA is what gets a project rate-limited or blocked."""
    from src.sources.reddit import USER_AGENT

    assert "global-equity-intelligence" in USER_AGENT
    assert len(USER_AGENT) > 40


# -- Reddit authentication ------------------------------------------------
#
# The 2026-09-29 run got "403 Client Error: Blocked" on all five attempts:
# Reddit blocks anonymous requests from datacentre addresses. A registered
# script app is free and works from anywhere.


def test_without_credentials_it_stays_anonymous_and_says_so(monkeypatch):
    monkeypatch.delenv("REDDIT_CLIENT_ID", raising=False)
    monkeypatch.delenv("REDDIT_CLIENT_SECRET", raising=False)

    source = _reddit()

    assert not source.authenticated
    assert source.base_url == "https://www.reddit.com"
    assert source.access_token() is None
    assert "Authorization" not in source.request_headers()


def test_with_credentials_it_uses_the_oauth_host_and_bearer_token(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id-not-real")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret-not-real")

    seen = {}

    class _Token:
        def json(self):
            return {"access_token": "tok-123", "expires_in": 86400}

    def fake_post(url, **kwargs):
        seen.update(url=url, auth=kwargs.get("auth"), data=kwargs.get("data"))
        return _Token()

    source = _reddit()
    monkeypatch.setattr(source.client, "post", fake_post)

    assert source.authenticated
    assert source.access_token() == "tok-123"
    assert seen["url"].endswith("/api/v1/access_token")
    assert seen["auth"] == ("id-not-real", "secret-not-real")
    assert seen["data"] == {"grant_type": "client_credentials"}
    assert source.request_headers()["Authorization"] == "bearer tok-123"
    assert source.base_url == "https://oauth.reddit.com"
    assert "oauth.reddit.com" in source.build_url("HexL")


def test_the_token_is_fetched_once_per_run(monkeypatch):
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "secret")
    calls = {"n": 0}

    class _Token:
        def json(self):
            calls["n"] += 1
            return {"access_token": "tok"}

    source = _reddit()
    monkeypatch.setattr(source.client, "post", lambda url, **kw: _Token())

    source.access_token(); source.access_token(); source.access_token()

    assert calls["n"] == 1


def test_a_failed_token_exchange_degrades_rather_than_ending_the_source(monkeypatch):
    """Auth failing should cost the better endpoint, not the whole source."""
    monkeypatch.setenv("REDDIT_CLIENT_ID", "id")
    monkeypatch.setenv("REDDIT_CLIENT_SECRET", "wrong")

    source = _reddit()

    def explode(url, **kwargs):
        raise RuntimeError("401 Unauthorized")

    monkeypatch.setattr(source.client, "post", explode)

    assert source.access_token() is None
    assert source.base_url == "https://www.reddit.com", "falls back to public"
    assert source.errors, "and the failure is recorded, not swallowed"


def test_the_credential_variable_names_are_configurable(monkeypatch):
    monkeypatch.setenv("MY_REDDIT_ID", "abc")
    monkeypatch.setenv("MY_REDDIT_SECRET", "def")

    source = _reddit(client_id_env="MY_REDDIT_ID", client_secret_env="MY_REDDIT_SECRET")

    assert source.authenticated
