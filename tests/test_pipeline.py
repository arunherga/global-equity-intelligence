"""End-to-end pipeline behaviour, entirely offline."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.config import load_config
from src.main import Pipeline, build_parser, resolve_tickers
from src.models import Relationship
from src.profiles import load_watchlist


@pytest.fixture(scope="module")
def run_result():
    from scripts.generate_sample_report import load_fixture_articles

    config = load_config()
    watchlist = load_watchlist(config=config)
    pipeline = Pipeline(config, watchlist, watchlist.profiles)
    return pipeline.run(
        run_date=date(2026, 9, 22),
        since_days=2,
        dry_run=True,
        offline=True,
        resolve_links=False,
        articles_override=load_fixture_articles(
            now=datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc)
        ),
    )


def test_the_pipeline_produces_events(run_result):
    assert run_result.events
    assert all(e.stocks for e in run_result.events)


def test_every_watchlist_company_can_be_reached(run_result, watchlist):
    covered = {t for e in run_result.events for t in e.stocks}
    assert covered == set(watchlist.tickers), f"missing: {set(watchlist.tickers) - covered}"


def test_articles_about_one_development_became_one_event(run_result):
    order_events = [
        e for e in run_result.events
        if "1.2 GW" in e.title and "WAAREEENER" in e.stocks
    ]
    assert len(order_events) == 1
    assert order_events[0].article_count >= 3
    assert order_events[0].has_official_source()


def test_unrelated_news_produced_no_events(run_result):
    titles = " ".join(e.title.lower() for e in run_result.events)
    assert "cricket" not in titles
    assert "weather update" not in titles


def test_lookalike_company_never_reaches_ravel(run_result):
    for event in run_result.events:
        if "Ravel Electronics" in event.title:
            assert "RAVEL" not in event.stocks


def test_opinion_pieces_are_scored_down(run_result):
    for event in run_result.events:
        if "Should you buy" in event.title:
            assert all(i.impact_score < 5 for i in event.stocks.values())


def test_one_event_can_hold_several_stocks(run_result):
    multi = [e for e in run_result.events if len(e.stocks) > 1]
    assert multi, "cross-stock events should exist in the fixture set"


def test_no_indirect_event_is_labelled_direct(run_result):
    for event in run_result.events:
        for impact in event.stocks.values():
            if impact.relationship == Relationship.DIRECT:
                assert impact.exposures is not None


def test_every_impact_carries_its_reasoning(run_result):
    for event in run_result.events:
        for impact in event.stocks.values():
            assert impact.score_reasons
            assert impact.why_it_matters
            assert impact.watch_next
            assert 0.0 < impact.confidence <= 0.95


def test_stats_are_consistent(run_result):
    assert run_result.stats.articles_scanned >= run_result.stats.unique_articles
    assert run_result.stats.events_detected == len(run_result.events)
    assert run_result.stats.relevant_events >= run_result.stats.high_impact_events
    assert run_result.stats.high_impact_events >= run_result.stats.critical_events


def test_a_dry_run_writes_nothing(tmp_path):
    from scripts.generate_sample_report import load_fixture_articles

    config = load_config(overrides={"storage": {
        "events_dir": str(tmp_path / "events"),
        "daily_dir": str(tmp_path / "daily"),
        "seen_file": str(tmp_path / "seen.json"),
    }})
    watchlist = load_watchlist(config=config)
    Pipeline(config, watchlist, watchlist.profiles).run(
        run_date=date(2026, 9, 22), since_days=2, dry_run=True, offline=True,
        resolve_links=False, articles_override=load_fixture_articles(),
    )
    assert not (tmp_path / "events").exists()
    assert not (tmp_path / "daily").exists()
    assert not (tmp_path / "seen.json").exists()


def test_a_real_run_writes_events_report_and_daily_json(tmp_path):
    from scripts.generate_sample_report import load_fixture_articles

    config = load_config(overrides={
        "storage": {
            "events_dir": str(tmp_path / "events"),
            "daily_dir": str(tmp_path / "daily"),
            "profiles_dir": str(tmp_path / "profiles"),
            "seen_file": str(tmp_path / "seen.json"),
        },
        "report": {"directory": str(tmp_path / "reports")},
    })
    watchlist = load_watchlist(config=config)
    result = Pipeline(config, watchlist, watchlist.profiles).run(
        run_date=date(2026, 9, 22), since_days=2, dry_run=False, offline=True,
        resolve_links=False, articles_override=load_fixture_articles(),
    )
    assert (tmp_path / "reports" / "2026-09-22.md").exists()
    assert (tmp_path / "daily" / "2026-09-22.json").exists()
    assert (tmp_path / "events" / "index.json").exists()
    assert (tmp_path / "seen.json").exists()
    assert result.events


def test_the_second_run_of_a_day_adds_to_the_report(tmp_path):
    """Regression: an evening run must never erase the morning's report."""
    from scripts.generate_sample_report import load_fixture_articles

    config = load_config(overrides={
        "storage": {
            "events_dir": str(tmp_path / "events"),
            "daily_dir": str(tmp_path / "daily"),
            "profiles_dir": str(tmp_path / "profiles"),
            "seen_file": str(tmp_path / "seen.json"),
        },
        "report": {"directory": str(tmp_path / "reports")},
    })
    watchlist = load_watchlist(config=config)
    articles = load_fixture_articles()
    morning = articles[:20]
    evening = articles[20:]

    first = Pipeline(config, watchlist, watchlist.profiles).run(
        run_date=date(2026, 9, 22), since_days=2, dry_run=False, offline=True,
        resolve_links=False, articles_override=morning,
    )
    morning_ids = {e.event_id for e in first.events}
    assert morning_ids

    second = Pipeline(config, watchlist, watchlist.profiles).run(
        run_date=date(2026, 9, 22), since_days=2, dry_run=False, offline=True,
        resolve_links=False, articles_override=evening,
    )
    combined = {e.event_id for e in second.events}
    assert morning_ids <= combined, "morning events disappeared from the day's report"
    assert len(combined) > len(morning_ids)

    report = (tmp_path / "reports" / "2026-09-22.md").read_text(encoding="utf-8")
    assert "GLOBAL EQUITY INTELLIGENCE" in report


def test_already_seen_articles_are_not_reprocessed(tmp_path):
    from scripts.generate_sample_report import load_fixture_articles

    config = load_config(overrides={
        "storage": {
            "events_dir": str(tmp_path / "events"),
            "daily_dir": str(tmp_path / "daily"),
            "profiles_dir": str(tmp_path / "profiles"),
            "seen_file": str(tmp_path / "seen.json"),
        },
        "report": {"directory": str(tmp_path / "reports")},
    })
    watchlist = load_watchlist(config=config)
    articles = load_fixture_articles()
    pipeline = Pipeline(config, watchlist, watchlist.profiles)
    pipeline.run(run_date=date(2026, 9, 22), since_days=2, dry_run=False, offline=True,
                 resolve_links=False, articles_override=articles)
    again = Pipeline(config, watchlist, watchlist.profiles).run(
        run_date=date(2026, 9, 23), since_days=2, dry_run=True, offline=True,
        resolve_links=False, articles_override=articles,
    )
    assert again.events == [], "the same articles should not produce events twice"


def test_a_single_ticker_run_only_touches_that_ticker(tmp_path):
    from scripts.generate_sample_report import load_fixture_articles

    config = load_config()
    watchlist = load_watchlist(config=config)
    profiles = watchlist.select(["WAAREEENER"])
    result = Pipeline(config, watchlist, profiles).run(
        run_date=date(2026, 9, 22), since_days=2, dry_run=True, offline=True,
        resolve_links=False, articles_override=load_fixture_articles(),
    )
    covered = {t for e in result.events for t in e.stocks}
    assert covered == {"WAAREEENER"}


def test_source_failures_never_stop_the_run(tmp_path, monkeypatch):
    config = load_config()
    watchlist = load_watchlist(config=config)
    pipeline = Pipeline(config, watchlist, watchlist.profiles)

    class ExplodingSource:
        name = "exploding"

        def fetch(self, context):
            raise RuntimeError("the internet fell over")

        def record_error(self, *args):
            pass

        def outcome(self, articles, duration):
            from src.sources.base import SourceOutcome

            return SourceOutcome(name=self.name, ok=False, errors=["boom"])

    monkeypatch.setattr("src.main.build_sources", lambda *a, **k: [ExplodingSource()])
    from src.sources.base import CollectionContext

    articles, diagnostics = pipeline.collect(
        CollectionContext(profiles=watchlist.profiles, queries={})
    )
    assert articles == []
    assert diagnostics and not diagnostics[0].ok


# -- CLI --------------------------------------------------------------------


def test_cli_accepts_the_documented_flags():
    parser = build_parser()
    args = parser.parse_args(["--ticker", "WAAREEENER,SUPRIYA", "--backfill-days", "730",
                              "--slice-days", "14", "--dry-run", "--verbose"])
    assert args.ticker == "WAAREEENER,SUPRIYA"
    assert args.backfill_days == 730
    assert args.slice_days == 14
    assert args.dry_run and args.verbose


def test_cli_ticker_resolution_normalises(watchlist):
    profiles = resolve_tickers(watchlist, "hdfc, coal india")
    assert [p.ticker for p in profiles] == ["COALINDIA", "HDFCBANK"]


def test_cli_rejects_an_unknown_ticker(watchlist):
    with pytest.raises(SystemExit):
        resolve_tickers(watchlist, "RELIANCE")


# -- stage isolation ------------------------------------------------------
#
# A live run meets text no fixture contains. These check that the two stages
# which touch arbitrary live input fail one item, not the whole run.


def _live_like_articles():
    """Two plainly matchable articles, stamped now so a live-style run keeps
    them (a live collection is filtered against the lookback window)."""
    from src.models import Article, utc_now

    now = utc_now()
    return [
        Article(
            title="Coal India digs beyond coal into batteries and critical minerals",
            url="https://example.com/coal-india-critical-minerals",
            source_name="Example",
            source_domain="example.com",
            published=now,
        ),
        Article(
            title="Waaree Energies subsidiary forays into specialty gases",
            url="https://example.com/waaree-specialty-gases",
            source_name="Example",
            source_domain="example.com",
            published=now,
        ),
    ]


def test_one_unmatchable_article_does_not_end_the_run(monkeypatch):
    config = load_config()
    watchlist = load_watchlist(config=config)
    pipeline = Pipeline(config, watchlist, watchlist.profiles)

    articles = _live_like_articles()
    poison = articles[0].title

    real = pipeline._match_one

    def explode(article):
        if article.title == poison:
            raise ValueError("unparseable headline")
        return real(article)

    monkeypatch.setattr(pipeline, "_match_one", explode)

    matched = pipeline.match(articles)

    assert [m.article.title for m in matched] == [articles[1].title]
    assert len(pipeline.match_failures) == 1
    title, err = pipeline.match_failures[0]
    assert title == poison
    assert "unparseable headline" in err


def test_link_resolution_failure_does_not_lose_the_run(monkeypatch):
    config = load_config()
    watchlist = load_watchlist(config=config)
    pipeline = Pipeline(config, watchlist, watchlist.profiles)

    def explode(*args, **kwargs):
        raise RuntimeError("aggregator went away mid-run")

    monkeypatch.setattr("src.main.resolve_article_urls", explode)

    # articles_override forces the run offline, which skips resolution
    # altogether - so stand in for collection instead, as a live run does.
    articles = _live_like_articles()
    monkeypatch.setattr(pipeline, "collect", lambda context, offline=False: (articles, []))

    result = pipeline.run(
        run_date=date.today(),
        since_days=2,
        dry_run=True,
        offline=False,          # so the resolution branch is entered
        resolve_links=True,
    )

    assert result.events, "events must survive a resolution failure"
    aborted = [d for d in result.diagnostics if d.source == "link_resolution"]
    assert aborted and not aborted[0].ok
    assert "aggregator went away mid-run" in aborted[0].note


# -- reading stored AI analysis from the CLI ------------------------------


def test_query_formats_stored_ai_analysis():
    from src.main import _format_ai_analysis

    lines = _format_ai_analysis({
        "provider": "gemini",
        "model": "gemini-3.1-flash-lite",
        "revenue_effect": "Converts to revenue over the delivery schedule.",
        "margin_effect": "",                      # empty fields are skipped
        "key_uncertainty": "Whether the order is firm.",
        "monitor_next": ["The exchange filing", "Q3 order inflow"],
    })

    text = "\n".join(lines)
    assert lines[0] == "-- AI analysis (gemini gemini-3.1-flash-lite)"
    assert "Revenue: Converts to revenue over the delivery schedule." in text
    assert "Margin:" not in text
    assert "Key uncertainty: Whether the order is firm." in text
    assert "     - The exchange filing" in text
    assert "     - Q3 order inflow" in text


def test_query_marks_which_events_carry_analysis(capsys):
    """[AI] in the listing, so it is obvious what --show-ai would print."""
    from src.main import _run_query, resolve_tickers
    from src.event_store import EventStore
    from src.models import Direction, Event, EventCategory, Relationship, StockImpact
    import argparse

    config = load_config()
    watchlist = load_watchlist(config=config)

    plain = Event(
        event_id="EVENT-COALINDIA-2026-9001",
        title="Routine production update",
        event_date=date(2026, 9, 27),
        event_types=[EventCategory.OTHER],
    )
    plain.stocks["COALINDIA"] = StockImpact(
        ticker="COALINDIA", relationship=Relationship.DIRECT, impact_score=6,
        direction=Direction.NEUTRAL, confidence=0.5,
    )
    analysed = Event(
        event_id="EVENT-COALINDIA-2026-9002",
        title="Rare earth exploration licence signed",
        event_date=date(2026, 9, 27),
        event_types=[EventCategory.OTHER],
    )
    analysed.stocks["COALINDIA"] = StockImpact(
        ticker="COALINDIA", relationship=Relationship.DIRECT, impact_score=11,
        direction=Direction.POSITIVE, confidence=0.7,
        ai_analysis={"provider": "gemini", "model": "m", "revenue_effect": "Unclear."},
    )

    class _Store:
        def query(self, **kwargs):
            return [plain, analysed]

    args = argparse.Namespace(query="COALINDIA", categories="", min_impact=0, show_ai=False)
    import src.main as main_mod
    original = main_mod.EventStore
    main_mod.EventStore = lambda *a, **k: type("S", (), {"load": lambda s: _Store()})()
    try:
        assert _run_query(config, watchlist, args) == 0
    finally:
        main_mod.EventStore = original

    out = capsys.readouterr().out
    assert "2 event(s) for COALINDIA, 1 carrying AI analysis" in out
    assert "EVENT-COALINDIA-2026-9002  11/15 POSITIVE DIRECT  [AI]" in out
    assert "9001" in out and "[AI]" not in out.split("9001")[1].split("\n")[0]
    assert "Add --show-ai" in out


def test_a_quiet_ai_run_is_not_reported_as_a_failure(monkeypatch):
    """Regression for a diagnostic that lied.

    The 27 Sep manual run built 17 events, the best scoring 8 against a
    threshold of 9. No call was made, yet Run Diagnostics said
    `ai_enrichment FAILED | attempted 20` - which reads as a broken key and
    is what it took an investigation to rule out. `attempted` also counted
    events in the run rather than calls made.
    """
    from src.ai import EnrichmentOutcome

    config = load_config()
    watchlist = load_watchlist(config=config)
    pipeline = Pipeline(config, watchlist, watchlist.profiles)

    articles = _live_like_articles()
    monkeypatch.setattr(pipeline, "collect", lambda context, offline=False: (articles, []))
    monkeypatch.setattr(
        "src.ai.enrich",
        lambda events, config, watchlist: EnrichmentOutcome(selected=0, enriched=0),
    )

    result = pipeline.run(
        run_date=date.today(), since_days=2, dry_run=True,
        offline=False, resolve_links=False,
    )

    diag = next(d for d in result.diagnostics if d.source == "ai_enrichment")
    assert diag.ok is True, "nothing qualifying is the threshold working"
    assert diag.attempted == 0, "attempted counts calls, not events in the run"
    assert "no event reached min_impact_score 9" in diag.note


def test_ai_that_qualified_and_failed_everything_is_a_failure(monkeypatch):
    from src.ai import EnrichmentOutcome

    config = load_config()
    watchlist = load_watchlist(config=config)
    pipeline = Pipeline(config, watchlist, watchlist.profiles)

    articles = _live_like_articles()
    monkeypatch.setattr(pipeline, "collect", lambda context, offline=False: (articles, []))
    monkeypatch.setattr(
        "src.ai.enrich",
        lambda events, config, watchlist: EnrichmentOutcome(
            selected=3, enriched=0,
            errors=["COALINDIA: 429 from gemini: Quota exceeded"] * 3,
        ),
    )

    result = pipeline.run(
        run_date=date.today(), since_days=2, dry_run=True,
        offline=False, resolve_links=False,
    )

    diag = next(d for d in result.diagnostics if d.source == "ai_enrichment")
    assert diag.ok is False
    assert diag.attempted == 3
    assert "Quota exceeded" in diag.note
    # Repeated identical errors collapse rather than filling the cell.
    assert diag.note.count("Quota exceeded") == 1


def test_consumer_posts_never_become_events(monkeypatch):
    """A shampoo thread is not a development.

    Reddit items flow through the same collection path as news, so without
    this they would cluster, score and fill the report - burying the filings
    it exists for.
    """
    from src.models import Article, SourceType

    config = load_config()
    watchlist = load_watchlist(config=config)
    pipeline = Pipeline(config, watchlist, watchlist.profiles)

    news, posts = _live_like_articles(), []
    for n in range(4):
        post = Article(
            title=f"Ravel PRO zero hairfall shampoo review, week {n}",
            url=f"https://www.reddit.com/r/IndianHaircare/comments/{n}/x/",
            source_name="r/IndianHaircare", source_domain="reddit.com",
            source_type=SourceType.SOCIAL_MEDIA,
        )
        post.raw["consumer"] = True
        posts.append(post)

    monkeypatch.setattr(
        pipeline, "collect", lambda context, offline=False: (news + posts, [])
    )

    result = pipeline.run(
        run_date=date.today(), since_days=2, dry_run=True,
        offline=True, resolve_links=False,
    )

    titles = " ".join(s.title for e in result.events for s in e.sources)
    assert "shampoo review" not in titles
    assert len(pipeline.consumer_articles) == 4
    diag = next(d for d in result.diagnostics if d.source == "consumer_signal")
    assert diag.ok is True and diag.articles == 4


def test_the_same_consumer_post_from_two_searches_counts_once():
    """Two searches finding one post is one post; two people is two."""
    from src.main import _split_consumer
    from src.models import Article

    def post(url, title):
        article = Article(title=title, url=url, source_domain="reddit.com")
        article.raw["consumer"] = True
        return article

    news = Article(title="Coal India signs a licence", url="https://x.test/1",
                   source_domain="x.test")

    consumer, remaining = _split_consumer([
        post("https://www.reddit.com/r/a/comments/1/x/", "Ravel PRO review"),
        post("https://www.reddit.com/r/a/comments/1/x/", "Ravel PRO review"),
        post("https://www.reddit.com/r/b/comments/2/y/", "Ravel PRO review"),
        news,
    ])

    assert len(consumer) == 2, "same URL folded, different people kept"
    assert remaining == [news]


def test_an_events_genre_is_judged_on_all_its_coverage_not_todays_slice(tmp_path):
    """The RBI liquidity auction scored 10/15 for ten days running.

    "variable rate reverse repo" was in the routine markers, the classifier
    set the flag, and the cluster vote still said no - because the event had
    lived since 22 September with twenty sources, while each run contributes
    only what arrived that morning. On a day whose one new headline read
    "RBI absorbs Rs 71,971 crore liquidity from banks", the vote saw a
    sample of one, and the penalty never applied.

    A replay cannot reproduce this: rebuilt from scratch, the cluster holds
    all twenty articles and the vote passes. It only happens against a
    persisted store, so that is what this builds.
    """
    from datetime import date as _date, datetime as _dt, timezone as _tz
    from src.classify import classify_article
    from src.event_cluster import MatchedArticle
    from src.event_store import EventStore
    from src.main import Pipeline, _cluster_flags
    from src.models import Article, Event, EventSource, Relationship, SourceType
    from src.relationship import StockLink

    config = load_config()
    watchlist = load_watchlist(config=config)
    pipeline = Pipeline(config, watchlist, watchlist.profiles)

    # Today's contribution: one headline that names no routine marker.
    todays = Article(
        title="RBI absorbs Rs 71,971 crore liquidity from banks",
        url="https://example.test/today", source_domain="example.test",
        published=_dt(2026, 10, 1, 9, tzinfo=_tz.utc),
    )
    matched = MatchedArticle(
        article=todays,
        links=[StockLink(ticker="TMB", relationship=Relationship.INDIRECT, entity=None,
                         exposures=[], reasons=[], evidence_weight=2.0)],
        classification=classify_article(todays),
    )

    class _Cluster:
        articles = [matched]
        lead = matched

    assert not matched.classification.routine_release, "today's headline alone looks ordinary"
    assert _cluster_flags(_Cluster())["routine_release"] is False, "which is the bug"

    # The same event's earlier coverage, as the store holds it.
    earlier = [
        "RBI to conduct Overnight Variable Rate Reverse Repo (VRRR) auction under LAF",
        "Money Market Operations as on September 21, 2026",
        "RBI absorbs Rs 71,971 cr via overnight VRRR auction amid surplus liquidity",
    ]

    flags = _cluster_flags(_Cluster(), earlier_titles=earlier)

    assert flags["routine_release"] is True, (
        "the event is a routine release, and its own archive says so"
    )


def test_earlier_coverage_cannot_suppress_a_genuinely_new_development(tmp_path):
    """An event that turns into real news must not stay suppressed.

    A company whose routine filings clustered earlier can still announce
    something; a third of all coverage is the bar, so a run of ordinary
    notices does not permanently gag it.
    """
    from datetime import datetime as _dt, timezone as _tz
    from src.classify import classify_article
    from src.event_cluster import MatchedArticle
    from src.main import _cluster_flags
    from src.models import Article, Relationship
    from src.relationship import StockLink

    real = []
    for n in range(6):
        article = Article(
            title=f"Coal India signs a binding supply agreement, tranche {n}",
            url=f"https://example.test/{n}", source_domain="example.test",
            published=_dt(2026, 10, 1, 9, tzinfo=_tz.utc),
        )
        real.append(MatchedArticle(
            article=article,
            links=[StockLink(ticker="COALINDIA", relationship=Relationship.DIRECT,
                             entity=None, exposures=[], reasons=[], evidence_weight=4.0)],
            classification=classify_article(article),
        ))

    class _Cluster:
        articles = real
        lead = real[0]

    flags = _cluster_flags(_Cluster(), earlier_titles=["Money Market Operations as on 21 September"])

    assert flags["routine_release"] is False, "one old notice must not bury six real reports"


def test_two_clusters_that_merge_into_one_stored_event_are_recorded_once(tmp_path):
    """Regression for the duplicate that led the 6 Oct report.

    Two of a run's clusters can both match the same stored event - two wire
    copies of one notice, say. The persistence step appended the merged
    event once per match, so the run ended holding it twice, and the report
    printed its tickers twice with it: "HDFCBANK ... also affects HDFCBANK,
    TMB, TMB". One stored event must come back exactly once.
    """
    from datetime import date as _date, datetime, timezone

    from src.event_store import EventStore
    from src.models import (
        Direction, Event, EventCategory, Relationship, RunResult, StockImpact,
    )
    from src.seen import SeenStore

    config = load_config(overrides={"storage": {
        "events_dir": str(tmp_path / "events"),
        "daily_dir": str(tmp_path / "daily"),
        "seen_file": str(tmp_path / "seen.json"),
    }, "report": {"directory": str(tmp_path / "reports")}})
    watchlist = load_watchlist(config=config)

    def make(event_id, title):
        event = Event(
            event_id=event_id, title=title, event_date=_date(2026, 10, 6),
            event_types=[EventCategory.LEGAL],
        )
        event.stocks["HDFCBANK"] = StockImpact(
            ticker="HDFCBANK", relationship=Relationship.DIRECT, impact_score=13,
            direction=Direction.UNCERTAIN, confidence=0.8,
        )
        event.stocks["TMB"] = StockImpact(
            ticker="TMB", relationship=Relationship.INDIRECT, impact_score=8,
            direction=Direction.UNCERTAIN, confidence=0.6,
        )
        return event

    store = EventStore(config.storage_path("events_dir")).load()
    store.save(make("EVENT-HDFCBANK-2026-0001", "HDFC Bank class action notice filed"))
    store.save_index()

    # Two fresh clusters, near-identical, both of which match the stored one.
    result = RunResult(
        run_date=_date(2026, 10, 6),
        started_at=datetime(2026, 10, 6, 6, 0, tzinfo=timezone.utc),
        events=[
            make("EVENT-HDFCBANK-2026-0002", "HDFC Bank class action notice filed"),
            make("EVENT-HDFCBANK-2026-0003", "HDFC Bank class action notice filed"),
        ],
        tickers=[p.ticker for p in watchlist.profiles],
    )

    pipeline = Pipeline(config, watchlist, watchlist.profiles)
    pipeline._persist(
        result,
        EventStore(config.storage_path("events_dir")).load(),
        SeenStore(config.storage_path("seen_file")),
        [],
    )

    ids = [e.event_id for e in result.events]
    assert len(ids) == len(set(ids)), ids
