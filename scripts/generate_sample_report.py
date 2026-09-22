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

BANNER = """> **This is a sample, not a briefing.** Every article below is fabricated and
> every outlet is fictional. The file exists to show the report's structure and
> to exercise the pipeline offline. Nothing here happened.

"""


def load_fixture_articles(path: Path = FIXTURE, now: datetime | None = None) -> List[Article]:
    """Turn the fixture file into Articles dated relative to now."""
    moment = now or datetime.now(timezone.utc)
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

    run_date = date.fromisoformat(args.date) if args.date else date.today()
    config = load_config()
    watchlist = load_watchlist(config=config)

    pipeline = Pipeline(config, watchlist, watchlist.profiles)
    result = pipeline.run(
        run_date=run_date,
        since_days=2,
        dry_run=True,                       # never touches the real data store
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
