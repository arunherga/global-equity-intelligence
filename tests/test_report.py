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
