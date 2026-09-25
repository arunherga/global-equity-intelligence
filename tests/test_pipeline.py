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
