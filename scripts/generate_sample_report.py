#!/usr/bin/env python3
"""Generate docs/sample-report.md from fabricated fixtures.

This runs the real pipeline — matching, relationship detection, clustering,
scoring, direction — over a fixed set of INVENTED articles from FICTIONAL
outlets, so the report's shape can be reviewed without a network and without
anyone mistaking the contents for news.

    python scripts/generate_sample_report.py
    python scripts/generate_sample_report.py --out docs/sample-report.md
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import List

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_config                      # noqa: E402
from src.main import Pipeline                           # noqa: E402
from src.models import Article, SourceType              # noqa: E402
from src.profiles import load_watchlist                 # noqa: E402
from src.report import build_report                     # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "sample_articles.json"

# The sample is built from a fixed set of invented articles, so it must render
# identically whatever day it is generated and whatever is already on disk:
# CI checks the committed file with `git diff --exit-code`.
#
# Two things made that check fail. Dating the report with today's date made it
# differ on every day after the one it was written. And event ids come from
# EventStore.next_sequence(), which counts what is already in data/events/ -
# so once a real run had committed events, the sample regenerated with ids
# like EVENT-COALINDIA-2026-0092 instead of -0001, differently on every
# machine. The generator therefore runs against an empty, throwaway store.
SAMPLE_MOMENT = datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc)

BANNER = """> **This is a sample, not a briefing.** Every article below is fabricated and
> every outlet is fictional. The file exists to show the report's structure and
> to exercise the pipeline offline. Nothing here happened.

"""


def load_fixture_articles(path: Path = FIXTURE, now: datetime | None = None) -> List[Article]:
    """Turn the fixture file into Articles dated relative to a fixed moment."""
    moment = now or SAMPLE_MOMENT
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    articles: List[Article] = []
    for index, row in enumerate(payload["articles"]):
        domain = row["source_domain"]
        slug = re.sub(r"[^a-z0-9]+", "-", row["title"].lower()).strip("-")[:60]
        articles.append(
            Article(
                title=row["title"],
                url=f"https://{domain}/story/{index}-{slug}",
                source_name=row["source_name"],
                source_domain=domain,
                source_type=SourceType(row.get("source_type", "unknown_news_site")),
                summary=row.get("summary", ""),
                published=moment - timedelta(hours=int(row.get("hours_ago", 6))),
                collector="fixture",
                is_official=bool(row.get("is_official", False)),
            )
        )
    return articles


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs" / "sample-report.md")
    parser.add_argument("--date", default="")
    args = parser.parse_args(argv)

    run_date = date.fromisoformat(args.date) if args.date else SAMPLE_MOMENT.date()

    with tempfile.TemporaryDirectory(prefix="gei-sample-") as scratch:
        scratch_path = Path(scratch)
        # An empty store, so event numbering always starts at 0001 and the
        # real data/ directory is neither read nor written.
        config = load_config(overrides={"storage": {
            "events_dir": str(scratch_path / "events"),
            "daily_dir": str(scratch_path / "daily"),
            "profiles_dir": str(scratch_path / "profiles"),
            "backfill_dir": str(scratch_path / "backfill"),
            "seen_file": str(scratch_path / "seen.json"),
        }})
        watchlist = load_watchlist(config=config)

        pipeline = Pipeline(config, watchlist, watchlist.profiles)
        result = pipeline.run(
            run_date=run_date,
            since_days=2,
            dry_run=True,                   # belt and braces: writes nothing
            offline=True,
            resolve_links=False,
            articles_override=load_fixture_articles(),
        )

        report = BANNER + build_report(result, config, watchlist)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(report)

    print(f"wrote {args.out}")
    print(f"  events      {len(result.events)}")
    print(f"  with stocks {sum(1 for e in result.events if e.stocks)}")
    covered = sorted({t for e in result.events for t in e.stocks})
    print(f"  tickers hit {len(covered)}/10: {', '.join(covered)}")
    missing = [t for t in watchlist.tickers if t not in covered]
    if missing:
        print(f"  NO EVENTS for {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
