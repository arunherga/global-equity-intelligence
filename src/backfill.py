"""Historical backfill.

A daily agent only ever sees today. Backfill walks a past window in slices,
runs the same pipeline over each slice, and writes the events into the same
store, so the historical questions the system exists for can be asked of real
history rather than of a fortnight of live running.

Slices matter: news search engines return a bounded number of results per
query, so asking for 365 days at once returns the same handful of stories.
Fourteen-day slices keep each request's result set meaningful.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import List, Sequence

from .config import Config
from .models import Event, RunResult, RunStats, utc_now
from .profiles.loader import CompanyProfile, Watchlist
from .report import build_report, report_path, write_report

LOG = logging.getLogger("gei.backfill")


def run_backfill(
    config: Config,
    watchlist: Watchlist,
    profiles: Sequence[CompanyProfile],
    days: int,
    slice_days: int = 14,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Walk ``days`` backwards in ``slice_days`` slices. Returns an exit code."""
    from .main import Pipeline  # imported here to avoid a circular import

    slice_days = max(1, slice_days)
    today = date.today()
    slices: List[tuple[int, int]] = []
    offset = 0
    while offset < days:
        end = min(offset + slice_days, days)
        # (since_days, until_days) measured backwards from today
        slices.append((end, offset))
        offset = end

    LOG.info("backfilling %d days in %d slices of %d", days, len(slices), slice_days)

    all_events: dict[str, Event] = {}
    diagnostics = []
    stats = RunStats()
    started = utc_now()

    pipeline = Pipeline(config, watchlist, profiles, verbose=verbose)
    for index, (since_days, until_days) in enumerate(slices, start=1):
        window_start = today - timedelta(days=since_days)
        window_end = today - timedelta(days=until_days)
        LOG.info("slice %d/%d: %s .. %s", index, len(slices), window_start, window_end)

        result = pipeline.run(
            run_date=window_end,
            since_days=since_days,
            until_days=until_days,
            dry_run=True,          # slices are aggregated, then written once
            resolve_links=False,   # far too many links to resolve over a year
        )
        for event in result.events:
            all_events[event.event_id] = event
        diagnostics.extend(result.diagnostics)
        stats.articles_scanned += result.stats.articles_scanned
        stats.unique_articles += result.stats.unique_articles

    events = sorted(all_events.values(), key=lambda e: (-e.max_impact, e.event_id))
    minimum = int(config.get("scoring.report_min_impact", 5))
    high = int(config.get("scoring.high_impact_threshold", 8))
    critical = int(config.get("scoring.critical_threshold", 13))
    stats.events_detected = len(events)
    stats.relevant_events = sum(1 for e in events if e.max_impact >= minimum)
    stats.high_impact_events = sum(1 for e in events if e.max_impact >= high)
    stats.critical_events = sum(1 for e in events if e.max_impact >= critical)
    stats.international_events = sum(1 for e in events if e.is_international)
    stats.official_announcements = sum(1 for e in events if e.has_official_source())

    result = RunResult(
        run_date=today,
        started_at=started,
        finished_at=utc_now(),
        stats=stats,
        diagnostics=_collapse(diagnostics),
        events=events,
        tickers=[p.ticker for p in profiles],
        dry_run=dry_run,
    )

    print(f"\nbackfill: {len(events)} events over {days} days "
          f"({stats.articles_scanned} articles scanned)")

    if dry_run:
        print("dry run — nothing written\n")
        return 0

    from .event_store import EventStore

    store = EventStore(config.storage_path("events_dir")).load()
    window = int(config.get("clustering.event_update_window_days", 21))
    threshold = float(config.get("clustering.event_update_similarity", 0.55))
    for event in events:
        existing = store.find_existing(event, window_days=window, similarity_threshold=threshold)
        store.save(store.merge(existing, event) if existing else event)
    store.save_index()

    backfill_dir = config.storage_path("backfill_dir")
    backfill_dir.mkdir(parents=True, exist_ok=True)
    payload_path = backfill_dir / f"backfill-{today.isoformat()}-{days}d.json"
    with open(payload_path, "w", encoding="utf-8") as handle:
        json.dump(result.to_dict(), handle, indent=2, ensure_ascii=False)

    report = build_report(result, config, watchlist)
    path = report_path(config, today, subdir="backfill")
    write_report(report, path)
    print(f"report  {path}\ndata    {payload_path}\n")
    return 0


def _collapse(diagnostics) -> list:
    """One row per source across all slices, so the table stays readable."""
    from .models import SourceDiagnostic

    merged: dict[str, SourceDiagnostic] = {}
    for diagnostic in diagnostics:
        current = merged.get(diagnostic.source)
        if current is None:
            merged[diagnostic.source] = SourceDiagnostic(
                source=diagnostic.source,
                ok=diagnostic.ok,
                attempted=diagnostic.attempted,
                succeeded=diagnostic.succeeded,
                articles=diagnostic.articles,
                duration_s=diagnostic.duration_s,
                errors=list(diagnostic.errors[:2]),
                note=diagnostic.note,
            )
            continue
        current.ok = current.ok or diagnostic.ok
        current.attempted += diagnostic.attempted
        current.succeeded += diagnostic.succeeded
        current.articles += diagnostic.articles
        current.duration_s = round(current.duration_s + diagnostic.duration_s, 2)
        for error in diagnostic.errors:
            if error not in current.errors and len(current.errors) < 3:
                current.errors.append(error)
    return list(merged.values())
