"""Pipeline entry point and CLI.

    python -m src.main                       # today's intelligence run
    python -m src.main --dry-run --verbose   # collect and report, write nothing
    python -m src.main --ticker WAAREEENER
    python -m src.main --ticker WAAREEENER,SUPRIYA,COALINDIA
    python -m src.main --backfill-days 365 --slice-days 14
    python -m src.main --check-sources       # probe every source, print a table
    python -m src.main --query WAAREEENER --categories TARIFF

The pipeline is the architecture in code::

    sources -> normalize -> dedupe -> seen -> entity + exposure matching
            -> relationship -> classify -> cluster -> impact -> direction
            -> event store -> daily JSON -> Markdown report

Every stage is isolated: a source that fails is recorded in Run Diagnostics
and the run continues.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .classify import Classification, RuleClassifier, classify_article
from .config import Config, load_config
from .deduplicate import deduplicate
from .direction import analyse_direction
from .entity_match import EntityRejection, match_entity
from .event_cluster import (
    Cluster,
    MatchedArticle,
    build_event,
    cluster_articles,
    make_event_id,
)
from .event_store import EventStore
from .exposure_match import match_exposures
from .impact import (
    ScoreInput,
    apply_relationship_caps,
    business_impacts,
    score_confidence,
    score_impact,
    time_horizon,
    watch_next_items,
    why_it_matters,
)
from .matching import fold
from .models import (
    Article,
    Direction,
    Event,
    Relationship,
    RunResult,
    RunStats,
    SourceDiagnostic,
    StockImpact,
    utc_now,
)
from .normalize import normalize_all, within_lookback
from .profiles.loader import CompanyProfile, Watchlist, load_watchlist
from .query_generator import generate_queries
from .relationship import determine_relationship
from .report import build_report, report_path, write_report
from .resolve import resolve_article_urls
from .seen import SeenStore
from .sources import CollectionContext, HttpClient, build_sources

LOG = logging.getLogger("gei")


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


class Pipeline:
    def __init__(
        self,
        config: Config,
        watchlist: Watchlist,
        profiles: Sequence[CompanyProfile],
        verbose: bool = False,
    ) -> None:
        self.config = config
        self.watchlist = watchlist
        self.profiles = list(profiles)
        self.verbose = verbose
        self.classifier = RuleClassifier()
        self.client = HttpClient.from_config(config.http)
        self.rejections: List[EntityRejection] = []

    # -- 1. collection ---------------------------------------------------
    def collect(
        self, context: CollectionContext, offline: bool = False
    ) -> Tuple[List[Article], List[SourceDiagnostic]]:
        if offline:
            return [], [SourceDiagnostic(source="(offline)", ok=True, note="network disabled")]

        articles: List[Article] = []
        diagnostics: List[SourceDiagnostic] = []
        for source in build_sources(self.config, self.client, context.tickers):
            started = time.monotonic()
            try:
                found = source.fetch(context)
            except Exception as exc:  # noqa: BLE001 - isolation is the point
                LOG.warning("source %s failed hard: %s", source.name, exc)
                source.record_error("unhandled failure", exc)
                found = []
            duration = time.monotonic() - started
            outcome = source.outcome(len(found), duration)
            diagnostics.append(
                SourceDiagnostic(
                    source=outcome.name,
                    ok=outcome.ok,
                    attempted=outcome.attempted,
                    succeeded=outcome.succeeded,
                    articles=outcome.articles,
                    duration_s=outcome.duration_s,
                    errors=outcome.errors[:5],
                    note="; ".join(outcome.notes[:3]),
                )
            )
            LOG.info(
                "%s: %d articles from %d attempts in %.1fs (%d errors)",
                outcome.name, outcome.articles, outcome.attempted,
                outcome.duration_s, len(outcome.errors),
            )
            articles.extend(found)
        return articles, diagnostics

    # -- 2. matching -----------------------------------------------------
    def match(self, articles: Sequence[Article]) -> List[MatchedArticle]:
        matched: List[MatchedArticle] = []
        for article in articles:
            classification = self.classifier.classify(article)
            text = fold(article.text)
            headline = fold(article.title)
            links = []
            for profile in self.profiles:
                entity, rejected = match_entity(article, profile, text, headline)
                self.rejections.extend(rejected)
                exposure = match_exposures(article, profile, text, headline)
                link = determine_relationship(
                    article,
                    profile,
                    entity,
                    exposure if exposure.matches else None,
                    classification,
                )
                if link is None:
                    continue
                if link.relationship == Relationship.WEAK and entity is None:
                    # Weak links are kept out of clustering entirely; they are
                    # noise and they pollute cluster ticker sets.
                    continue
                links.append(link)
            if links:
                matched.append(
                    MatchedArticle(
                        article=article, links=links, classification=classification
                    )
                )
        return matched

    # -- 3. events -------------------------------------------------------
    def build_events(self, matched: Sequence[MatchedArticle], store: EventStore) -> List[Event]:
        clusters = cluster_articles(
            matched,
            threshold=float(self.config.get("clustering.title_similarity_threshold", 0.62)),
            window_days=int(self.config.get("clustering.same_event_window_days", 3)),
        )
        quality_map = self.config.source_quality
        events: List[Event] = []
        sequences: Dict[Tuple[str, int], int] = {}

        for cluster in clusters:
            scope_id = make_event_id(cluster, 1)
            scope = scope_id.split("-")[1]
            year = cluster.event_day.year
            key = (scope, year)
            if key not in sequences:
                sequences[key] = store.next_sequence(scope, year)
            event_id = make_event_id(cluster, sequences[key], year)
            sequences[key] += 1

            event = build_event(cluster, event_id, quality_map)
            self.score_event(event, cluster)
            if event.stocks:
                events.append(event)
        return events

    # -- 4. scoring ------------------------------------------------------
    def score_event(self, event: Event, cluster: Cluster) -> None:
        quality_map = self.config.source_quality
        caps = self.config.section("scoring")
        source_types = tuple(s.source_type for s in event.sources)
        independent = len({s.source_domain for s in event.sources if s.source_domain}) or 1

        # Strongest link per ticker across the cluster's articles.
        best_links: Dict[str, Tuple[MatchedArticle, "object"]] = {}
        for matched in cluster.articles:
            for link in matched.links:
                current = best_links.get(link.ticker)
                if current is None or link.relationship.rank > current[1].relationship.rank:
                    best_links[link.ticker] = (matched, link)

        for ticker, (matched, link) in best_links.items():
            profile = self.watchlist.get(ticker)
            classification = matched.classification or Classification()
            data = ScoreInput(
                event=event,
                link=link,
                classification=classification,
                profile=profile,
                text=matched.article.summary,
                headline=matched.article.title,
                source_types=source_types,
                independent_sources=independent,
            )
            score, reasons = score_impact(data)
            score = apply_relationship_caps(score, link.relationship, caps, reasons)
            confidence, confidence_reasons = score_confidence(data, quality_map)
            direction, direction_reasons = analyse_direction(
                matched.article.title, matched.article.summary, link, classification, profile
            )
            categories = event.event_types or classification.categories
            impacts = business_impacts(categories, link, profile)

            event.stocks[ticker] = StockImpact(
                ticker=ticker,
                relationship=link.relationship,
                impact_score=score,
                direction=direction,
                confidence=confidence,
                business_impacts=impacts,
                time_horizon=time_horizon(categories, link.relationship),
                exposures=link.exposures[:6],
                score_reasons=reasons,
                direction_reasons=direction_reasons,
                confidence_reasons=confidence_reasons,
                watch_next=watch_next_items(categories, link),
                why_it_matters=why_it_matters(link, categories, impacts, profile),
            )

    # -- 5. whole run ----------------------------------------------------
    def run(
        self,
        run_date: date,
        since_days: int,
        until_days: int = 0,
        dry_run: bool = False,
        offline: bool = False,
        resolve_links: bool = True,
        articles_override: Optional[Sequence[Article]] = None,
    ) -> RunResult:
        started = utc_now()
        queries = generate_queries(self.profiles, budget=self.config.max_queries_per_ticker)
        context = CollectionContext(
            profiles=self.profiles,
            queries=queries,
            since_days=since_days,
            until_days=until_days,
            max_articles_per_query=self.config.max_articles_per_query,
            dry_run=dry_run,
        )

        if articles_override is not None:
            # Used by the sample-report script and the offline tests: the same
            # pipeline, with collection replaced by a fixed article set.
            raw = list(articles_override)
            diagnostics = [
                SourceDiagnostic(
                    source="(fixture)",
                    ok=True,
                    attempted=len(raw),
                    succeeded=len(raw),
                    articles=len(raw),
                    note="articles supplied directly; no network used",
                )
            ]
            offline = True
        else:
            raw, diagnostics = self.collect(context, offline=offline)
        articles = normalize_all(raw, self.config)
        LOG.info("collected %d articles, %d after normalisation", len(raw), len(articles))

        lookback_hours = max(self.config.lookback_hours, since_days * 24)
        if until_days == 0:
            articles = [
                a for a in articles
                if within_lookback(a, lookback_hours, now=datetime.now(timezone.utc))
            ]

        deduped = deduplicate(
            articles,
            similarity_threshold=float(
                self.config.get("deduplication.title_similarity_threshold", 0.88)
            ),
            window_days=int(self.config.get("deduplication.window_days", 5)),
            quality_map=self.config.source_quality,
        )
        LOG.info("deduplicated: %d kept, %d removed", deduped.kept, deduped.removed)

        seen = SeenStore(
            self.config.storage_path("seen_file"),
            int(self.config.get("storage.seen_retention_days", 45)),
        ).load()
        fresh, previously_seen = seen.split(deduped.articles)
        LOG.info("%d fresh articles, %d already seen", len(fresh), len(previously_seen))

        matched = self.match(fresh)
        LOG.info("%d articles matched to at least one company", len(matched))

        if resolve_links and not offline and matched:
            # Only resolve links that can still reach the report.
            candidates = [m.article for m in matched]
            _, resolved, attempted, notes = resolve_article_urls(
                candidates, self.client, limit=int(self.config.get("run.resolve_limit", 40))
            )
            diagnostics.append(
                SourceDiagnostic(
                    source="link_resolution",
                    ok=resolved > 0 or attempted == 0,
                    attempted=attempted,
                    succeeded=resolved,
                    articles=resolved,
                    note="; ".join(notes[:2]) or f"{resolved}/{attempted} aggregator links resolved",
                )
            )

        store = EventStore(self.config.storage_path("events_dir")).load()
        events = self.build_events(matched, store)
        LOG.info("%d events built", len(events))

        if self.config.ai_enabled and events:
            from .ai import enrich

            enriched = enrich(events, self.config, self.watchlist)
            LOG.info("AI enrichment applied to %d event/stock pairs", enriched)
            diagnostics.append(
                SourceDiagnostic(
                    source="ai_enrichment",
                    ok=enriched > 0,
                    attempted=len(events),
                    succeeded=enriched,
                    articles=enriched,
                    note=f"provider={self.config.get('ai.provider')} "
                         f"model={self.config.get('ai.model')}",
                )
            )

        stats = RunStats(
            articles_scanned=len(raw),
            unique_articles=deduped.kept,
            events_detected=len(events),
            international_events=sum(1 for e in events if e.is_international),
            official_announcements=sum(1 for e in events if e.has_official_source()),
        )
        high = int(self.config.get("scoring.high_impact_threshold", 8))
        critical = int(self.config.get("scoring.critical_threshold", 13))
        minimum = int(self.config.get("scoring.report_min_impact", 5))
        stats.relevant_events = sum(1 for e in events if e.max_impact >= minimum)
        stats.high_impact_events = sum(1 for e in events if e.max_impact >= high)
        stats.critical_events = sum(1 for e in events if e.max_impact >= critical)

        result = RunResult(
            run_date=run_date,
            started_at=started,
            finished_at=utc_now(),
            stats=stats,
            diagnostics=diagnostics,
            events=events,
            tickers=[p.ticker for p in self.profiles],
            dry_run=dry_run,
        )

        if not dry_run:
            self._persist(result, store, seen, fresh)
        return result

    # -- persistence -----------------------------------------------------
    def _persist(
        self,
        result: RunResult,
        store: EventStore,
        seen: SeenStore,
        fresh: Sequence[Article],
    ) -> None:
        window = int(self.config.get("clustering.event_update_window_days", 21))
        threshold = float(self.config.get("clustering.event_update_similarity", 0.55))

        final_events: List[Event] = []
        for event in result.events:
            existing = store.find_existing(event, window_days=window, similarity_threshold=threshold)
            if existing is not None:
                merged = store.merge(existing, event)
                store.save(merged)
                final_events.append(merged)
            else:
                store.save(event)
                final_events.append(event)
        store.save_index()
        result.events = final_events

        seen.mark_all(fresh, when=result.run_date)
        seen.save()

        merged_events = self._merge_daily(result)
        result.events = merged_events

        daily_path = self.config.storage_path("daily_dir") / f"{result.run_date.isoformat()}.json"
        daily_path.parent.mkdir(parents=True, exist_ok=True)
        with open(daily_path, "w", encoding="utf-8") as handle:
            json.dump(result.to_dict(), handle, indent=2, ensure_ascii=False)

        report = build_report(result, self.config, self.watchlist)
        write_report(report, report_path(self.config, result.run_date))

    def _merge_daily(self, result: RunResult) -> List[Event]:
        """Fold this run's events into anything already recorded for today.

        The second run of the day must ADD to the morning report, never
        replace it. Merging happens on the event set and the report is then
        regenerated, which is far safer than editing Markdown.
        """
        daily_path = self.config.storage_path("daily_dir") / f"{result.run_date.isoformat()}.json"
        if not daily_path.exists():
            return result.events

        try:
            with open(daily_path, "r", encoding="utf-8") as handle:
                previous = json.load(handle)
        except (json.JSONDecodeError, OSError):
            return result.events

        by_id: Dict[str, Event] = {}
        for payload in previous.get("events", []):
            try:
                event = Event.from_dict(payload)
            except (KeyError, ValueError):
                continue
            by_id[event.event_id] = event

        for event in result.events:
            by_id[event.event_id] = event

        earlier = previous.get("stats", {})
        result.stats.articles_scanned += int(earlier.get("articles_scanned", 0))
        result.stats.unique_articles += int(earlier.get("unique_articles", 0))

        merged = list(by_id.values())
        merged.sort(key=lambda e: (-e.max_impact, e.event_id))
        result.stats.events_detected = len(merged)
        return merged


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description="Personal equity intelligence for a fixed 10-stock watchlist.",
    )
    parser.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    parser.add_argument("--watchlist", type=Path, default=None, help="path to watchlist.yaml")
    parser.add_argument(
        "--ticker",
        default="",
        help="restrict the run to one ticker or a comma-separated list (HDFC -> HDFCBANK)",
    )
    parser.add_argument("--dry-run", action="store_true", help="run fully, write nothing")
    parser.add_argument("--verbose", "-v", action="store_true", help="debug logging")
    parser.add_argument("--offline", action="store_true", help="skip all network calls")
    parser.add_argument(
        "--no-resolve", action="store_true", help="skip aggregator link resolution"
    )
    parser.add_argument("--date", default="", help="run date (YYYY-MM-DD), defaults to today")
    parser.add_argument(
        "--since-days", type=int, default=0, help="collection window; defaults to run.lookback_hours"
    )
    parser.add_argument("--backfill-days", type=int, default=0, help="historical backfill window")
    parser.add_argument("--slice-days", type=int, default=14, help="backfill slice size")
    parser.add_argument(
        "--check-sources", action="store_true", help="probe every source and print a table"
    )
    parser.add_argument("--rebuild-index", action="store_true", help="rebuild the event index")
    parser.add_argument("--query", default="", help="search the event store for a ticker")
    parser.add_argument("--categories", default="", help="comma-separated categories for --query")
    parser.add_argument("--min-impact", type=int, default=0, help="minimum impact for --query")
    parser.add_argument("--no-ai", action="store_true", help="force the AI layer off")
    return parser


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def resolve_tickers(watchlist: Watchlist, raw: str) -> List[CompanyProfile]:
    if not raw.strip():
        return watchlist.profiles
    # Split on commas only: "coal india" is one shorthand, not two tickers.
    wanted = [t.strip() for t in raw.split(",") if t.strip()]
    unknown = [t for t in wanted if t not in watchlist]
    if unknown:
        raise SystemExit(
            f"unknown ticker(s): {', '.join(unknown)}. "
            f"known: {', '.join(watchlist.tickers)}"
        )
    return watchlist.select(wanted)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)

    overrides = {"ai": {"enabled": False}} if args.no_ai else None
    config = load_config(args.config, overrides=overrides)
    watchlist = load_watchlist(args.watchlist, config=config)
    profiles = resolve_tickers(watchlist, args.ticker)

    if args.rebuild_index:
        store = EventStore(config.storage_path("events_dir"))
        count = store.rebuild_index()
        store.save_index()
        print(f"rebuilt index: {count} events")
        return 0

    if args.query:
        return _run_query(config, watchlist, args)

    if args.check_sources:
        from .diagnostics import check_sources

        return check_sources(config, profiles)

    run_date = date.fromisoformat(args.date) if args.date else date.today()

    if args.backfill_days:
        from .backfill import run_backfill

        return run_backfill(
            config=config,
            watchlist=watchlist,
            profiles=profiles,
            days=args.backfill_days,
            slice_days=args.slice_days,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )

    since_days = args.since_days or max(1, config.lookback_hours // 24)
    pipeline = Pipeline(config, watchlist, profiles, verbose=args.verbose)
    result = pipeline.run(
        run_date=run_date,
        since_days=since_days,
        dry_run=args.dry_run,
        offline=args.offline,
        resolve_links=not args.no_resolve,
    )

    _print_summary(result, config, watchlist, dry_run=args.dry_run)

    if not args.dry_run and config.get("alerts.enabled", False):
        from .alerts import dispatch_alerts

        dispatch_alerts(result, config)
    return 0


def _print_summary(
    result: RunResult, config: Config, watchlist: Watchlist, dry_run: bool
) -> None:
    stats = result.stats
    print()
    print(f"GLOBAL EQUITY INTELLIGENCE — {result.run_date.isoformat()}")
    print(f"  stocks monitored        {len(result.tickers)}")
    print(f"  articles scanned        {stats.articles_scanned}")
    print(f"  unique articles         {stats.unique_articles}")
    print(f"  events detected         {stats.events_detected}")
    print(f"  relevant events         {stats.relevant_events}")
    print(f"  high impact             {stats.high_impact_events}")
    print(f"  critical                {stats.critical_events}")
    print(f"  international           {stats.international_events}")
    print(f"  official announcements  {stats.official_announcements}")
    failed = [d.source for d in result.diagnostics if not d.ok]
    if failed:
        print(f"  failed sources          {', '.join(failed)}")
    if dry_run:
        print("\n  dry run — nothing written")
    else:
        print(f"\n  report  {report_path(config, result.run_date)}")
    print()


def _run_query(config: Config, watchlist: Watchlist, args) -> int:
    store = EventStore(config.storage_path("events_dir")).load()
    ticker = watchlist.normalize(args.query)
    categories = [c.strip() for c in args.categories.split(",") if c.strip()]
    events = store.query(ticker=ticker, categories=categories, min_impact=args.min_impact)
    if not events:
        print(f"no stored events for {ticker}")
        return 0
    print(f"{len(events)} event(s) for {ticker}")
    for event in events:
        impact = event.stocks.get(ticker)
        detail = (
            f"{impact.impact_score}/15 {impact.direction.value} {impact.relationship.value}"
            if impact
            else "-"
        )
        print(f"  {event.event_date} {event.event_id}  {detail}")
        print(f"      {event.title[:100]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
