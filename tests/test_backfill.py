"""Historical backfill."""

from __future__ import annotations

from datetime import date

import pytest

from src.backfill import _collapse, run_backfill
from src.config import load_config
from src.models import SourceDiagnostic
from src.profiles import load_watchlist


def test_slices_cover_the_window_without_gaps(monkeypatch, tmp_path):
    """A year requested in fortnightly slices must query every fortnight once."""
    seen = []

    class FakePipeline:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, run_date, since_days, until_days=0, **kwargs):
            from src.models import RunResult, RunStats, utc_now

            seen.append((since_days, until_days))
            return RunResult(run_date=run_date, started_at=utc_now(), stats=RunStats())

    monkeypatch.setattr("src.main.Pipeline", FakePipeline)

    config = load_config(overrides={
        "storage": {"events_dir": str(tmp_path / "events"),
                    "backfill_dir": str(tmp_path / "backfill")},
        "report": {"directory": str(tmp_path / "reports")},
    })
    watchlist = load_watchlist(config=config)
    run_backfill(config, watchlist, watchlist.profiles, days=60, slice_days=14, dry_run=True)

    assert seen == [(14, 0), (28, 14), (42, 28), (56, 42), (60, 56)]
    # every day between 0 and 60 is covered exactly once
    covered = set()
    for since, until in seen:
        covered |= set(range(until, since))
    assert covered == set(range(0, 60))


def test_backfill_writes_a_report_and_data(monkeypatch, tmp_path):
    from src.models import RunResult, RunStats, utc_now

    class FakePipeline:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, run_date, since_days, until_days=0, **kwargs):
            return RunResult(run_date=run_date, started_at=utc_now(), stats=RunStats())

    monkeypatch.setattr("src.main.Pipeline", FakePipeline)
    config = load_config(overrides={
        "storage": {"events_dir": str(tmp_path / "events"),
                    "backfill_dir": str(tmp_path / "backfill")},
        "report": {"directory": str(tmp_path / "reports")},
    })
    watchlist = load_watchlist(config=config)
    run_backfill(config, watchlist, watchlist.profiles, days=14, slice_days=14, dry_run=False)

    assert (tmp_path / "reports" / "backfill").exists()
    assert list((tmp_path / "backfill").glob("backfill-*.json"))


def test_diagnostics_collapse_to_one_row_per_source():
    rows = [
        SourceDiagnostic(source="google_news", ok=True, attempted=10, articles=20, duration_s=1.0),
        SourceDiagnostic(source="google_news", ok=False, attempted=10, articles=0,
                         duration_s=2.0, errors=["403"]),
        SourceDiagnostic(source="nse", ok=False, attempted=1, articles=0, errors=["blocked"]),
    ]
    collapsed = {d.source: d for d in _collapse(rows)}
    assert collapsed["google_news"].attempted == 20
    assert collapsed["google_news"].articles == 20
    assert collapsed["google_news"].ok is True
    assert collapsed["nse"].ok is False
