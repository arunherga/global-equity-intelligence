"""Report generation."""

from __future__ import annotations

from datetime import date

import pytest

from src.main import Pipeline
from src.report import ReportBuilder, build_report, section_for


@pytest.fixture(scope="module")
def result(request):
    from datetime import datetime, timezone

    from scripts.generate_sample_report import load_fixture_articles
    from src.config import load_config
    from src.profiles import load_watchlist

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


@pytest.fixture(scope="module")
def report(result):
    from src.config import load_config
    from src.profiles import load_watchlist

    config = load_config()
    return build_report(result, config, load_watchlist(config=config))


def test_all_required_sections_are_present(report):
    for heading in (
        "# GLOBAL EQUITY INTELLIGENCE",
        "## What needs attention",
        "## Watchlist dashboard",
        "## Global events affecting my stocks",
        "## Cross-stock events",
        "## Run diagnostics",
    ):
        assert heading in report


def test_every_watchlist_company_gets_a_section(report, watchlist):
    for ticker in watchlist.tickers:
        assert f"## {ticker} Intelligence" in report


def test_the_dashboard_is_not_a_ranking(report):
    assert "not a buy or sell list" in report.lower()


def test_no_buy_sell_or_hold_recommendation_appears(report):
    lowered = report.lower()
    for phrase in ("we recommend buying", "sell the stock", "target price of", "buy rating"):
        assert phrase not in lowered


def test_impact_and_direction_are_reported_separately(report):
    assert "**Impact**" in report
    assert "**Direction**" in report
    assert "**Confidence**" in report


def test_score_reasons_are_shown(report):
    assert "**Score reasons**" in report
    assert "+5 Direct company event" in report


def test_event_ids_are_quoted_for_lookup(report):
    assert "`EVENT-" in report


def test_diagnostics_are_honest_about_sources(report):
    assert "only described as working when it actually returned items" in report


def test_counts_in_the_header_are_real(result, report):
    assert f"| Articles scanned | {result.stats.articles_scanned:,} |" in report


def test_low_impact_events_are_filtered_out(result, config, watchlist):
    builder = ReportBuilder(config, watchlist)
    pairs = builder.reportable_pairs(result.events)
    assert pairs
    assert all(i.impact_score >= builder.min_impact for _, i in pairs)


def test_sections_route_events_sensibly(result, config, watchlist):
    builder = ReportBuilder(config, watchlist)
    for event, impact in builder.reportable_pairs(result.events):
        assert section_for(event, impact) in {
            "direct", "industry", "competitor", "chain",
            "international", "regulatory", "macro",
        }


def test_report_ends_with_the_disclaimer(report):
    assert "Nothing here is investment advice" in report.strip().splitlines()[-1] or \
           "Nothing here is investment advice" in report[-500:]


def test_report_is_written_with_unix_line_endings(tmp_path, report):
    from src.report import write_report

    path = tmp_path / "2026-09-22.md"
    write_report(report, path)
    assert b"\r\n" not in path.read_bytes()


# -- determinism ------------------------------------------------------------


def test_the_sample_report_does_not_depend_on_todays_date():
    """Regression: CI checks the committed sample with `git diff --exit-code`.

    The generator originally dated the sample with date.today(), so the file
    differed from the committed copy on every day after the one it was written
    on, and the tests workflow failed daily.
    """
    from scripts.generate_sample_report import (
        SAMPLE_MOMENT,
        load_fixture_articles,
        main,
    )

    articles = load_fixture_articles()
    assert all(a.published is not None for a in articles)
    # every fixture article is anchored to the pinned moment, not to now
    assert max(a.published for a in articles) <= SAMPLE_MOMENT


def test_regenerating_the_sample_is_byte_identical(tmp_path):
    from scripts.generate_sample_report import main

    first, second = tmp_path / "a.md", tmp_path / "b.md"
    main(["--out", str(first)])
    main(["--out", str(second)])
    assert first.read_bytes() == second.read_bytes()


def test_the_committed_sample_report_is_up_to_date(tmp_path):
    """What the tests workflow asserts, asserted here so it fails locally first."""
    from pathlib import Path

    from scripts.generate_sample_report import main

    committed = Path(__file__).resolve().parent.parent / "docs" / "sample-report.md"
    regenerated = tmp_path / "sample-report.md"
    main(["--out", str(regenerated)])
    assert regenerated.read_text(encoding="utf-8") == committed.read_text(encoding="utf-8"), (
        "docs/sample-report.md is stale; run "
        "`python scripts/generate_sample_report.py` and commit the result"
    )


def test_injected_fixtures_are_not_filtered_by_wall_clock_time():
    """The fixture corpus must not age out of the lookback window."""
    from datetime import date

    from src.config import load_config
    from src.main import Pipeline
    from src.profiles import load_watchlist
    from scripts.generate_sample_report import load_fixture_articles

    config = load_config()
    watchlist = load_watchlist(config=config)
    # A run dated years after the fixtures must still see all of them.
    result = Pipeline(config, watchlist, watchlist.profiles).run(
        run_date=date(2030, 1, 1), since_days=2, dry_run=True, offline=True,
        resolve_links=False, articles_override=load_fixture_articles(),
    )
    assert result.stats.unique_articles == 39
    assert result.events


# -- repetition ------------------------------------------------------------


def _event(event_id, title, tickers, score=10, commodity=None, categories=None):
    from datetime import date

    from src.models import (
        Direction,
        Event,
        EventCategory,
        EventSource,
        ExposureMatch,
        ExposureType,
        Relationship,
        SourceType,
        StockImpact,
    )

    event = Event(
        event_id=event_id,
        title=title,
        event_date=date(2026, 9, 22),
        event_types=list(categories or [EventCategory.COMMODITY]),
        sources=[EventSource(title=title, url=f"https://s.test/{event_id}",
                             source_name="Wire", source_domain="s.test",
                             source_type=SourceType.ESTABLISHED_NEWSPAPER)],
    )
    for ticker in tickers:
        exposures = []
        if commodity:
            exposures.append(ExposureMatch(
                exposure_type=ExposureType.COMMODITY, term=commodity,
                relationship=Relationship.INDIRECT_STRONG, weight=2.5))
        event.stocks[ticker] = StockImpact(
            ticker=ticker, relationship=Relationship.INDIRECT_STRONG,
            impact_score=score, direction=Direction.NEGATIVE, confidence=0.6,
            exposures=exposures, why_it_matters="because", watch_next=["a thing"],
            score_reasons=["+3 test"],
        )
    return event


def _render(events, config, watchlist):
    from datetime import date

    from src.models import RunResult, utc_now

    result = RunResult(run_date=date(2026, 9, 22), started_at=utc_now(),
                       events=list(events), tickers=watchlist.tickers)
    return build_report(result, config, watchlist)


def test_a_multi_stock_event_is_detailed_once_not_once_per_stock(config, watchlist):
    """Regression: the whole block was printed once per affected company."""
    report = _render([_event("EVENT-GLOBAL-2026-0001",
                             "RBI cuts the repo rate by 25 bps",
                             ["TMB", "HDFCBANK"], score=11)], config, watchlist)
    attention = report.split("## Watchlist dashboard")[0]
    assert attention.count("**Why it matters**") == 1
    assert attention.count("**Score reasons**") == 1
    assert "also affects HDFCBANK" in attention or "also affects TMB" in attention


def test_the_other_affected_stocks_are_still_named(config, watchlist):
    report = _render([_event("EVENT-GLOBAL-2026-0001", "RBI cuts the repo rate",
                             ["TMB", "HDFCBANK"], score=11)], config, watchlist)
    assert "**Also affects**" in report


def test_same_topic_events_collapse_into_one_line(config, watchlist):
    """Twelve diesel-price stories are one story for someone holding JKIPL."""
    events = [
        _event(f"EVENT-GLOBAL-2026-{i:04d}", f"Diesel prices hit a record, report {i}",
               ["JKIPL"], score=6, commodity="diesel prices")
        for i in range(1, 7)
    ]
    report = _render(events, config, watchlist)
    jkipl = report.split("## JKIPL Intelligence")[1].split("\n## ")[0]
    listed = [l for l in jkipl.splitlines() if l.startswith("- **[")]
    assert len(listed) == 1, f"expected one collapsed line, got {len(listed)}"
    assert "+5 similar reports" in listed[0]


def test_a_couple_of_related_events_are_not_collapsed(config, watchlist):
    """Below the threshold they are still listed individually."""
    events = [
        _event(f"EVENT-GLOBAL-2026-{i:04d}", f"Diesel prices move, report {i}",
               ["JKIPL"], score=6, commodity="diesel prices")
        for i in range(1, 3)
    ]
    report = _render(events, config, watchlist)
    jkipl = report.split("## JKIPL Intelligence")[1].split("\n## ")[0]
    assert len([l for l in jkipl.splitlines() if l.startswith("- **[")]) == 2


def test_unrelated_topics_never_collapse_together(config, watchlist):
    events = [
        _event("EVENT-GLOBAL-2026-0001", "Diesel prices hit a record", ["JKIPL"],
               score=6, commodity="diesel prices"),
        _event("EVENT-GLOBAL-2026-0002", "Steel prices climb", ["JKIPL"],
               score=6, commodity="steel prices"),
        _event("EVENT-GLOBAL-2026-0003", "Diesel prices climb again", ["JKIPL"],
               score=6, commodity="diesel prices"),
    ]
    report = _render(events, config, watchlist)
    jkipl = report.split("## JKIPL Intelligence")[1].split("\n## ")[0]
    assert "Steel prices climb" in jkipl


def test_an_event_detailed_above_is_marked_as_a_repeat(config, watchlist):
    report = _render([_event("EVENT-GLOBAL-2026-0001", "RBI cuts the repo rate",
                             ["TMB", "HDFCBANK"], score=11)], config, watchlist)
    per_stock = report.split("## TMB Intelligence")[1]
    assert "detailed above" in per_stock
