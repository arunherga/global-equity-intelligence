"""Source verification.

The rule for this project is that a source is never described as working
until it has actually returned items. ``--check-sources`` is how that claim
gets earned: it runs each collector against a deliberately small request and
prints what really happened, so README and docs can be written from evidence.
"""

from __future__ import annotations

import time
from typing import List, Sequence

from .config import Config
from .models import SourceDiagnostic
from .profiles.loader import CompanyProfile
from .query_generator import generate_queries
from .sources import CollectionContext, HttpClient, build_sources


def probe_sources(
    config: Config, profiles: Sequence[CompanyProfile], per_source_queries: int = 8
) -> List[SourceDiagnostic]:
    """Run every enabled source with a minimal workload."""
    client = HttpClient.from_config(config.http)
    profiles = list(profiles)[:2] or list(profiles)

    queries = generate_queries(profiles, budget=per_source_queries)
    # GDELT only runs international queries, so the probe must include at
    # least one or the source is reported as untried rather than working.
    for ticker, items in queries.items():
        if not any(q.international for q in items):
            from .query_generator import generate_for_profile

            extra = [
                q for q in generate_for_profile(
                    next(p for p in profiles if p.ticker == ticker), budget=40
                )
                if q.international
            ]
            if extra:
                items.append(extra[0])
    context = CollectionContext(
        profiles=profiles,
        queries=queries,
        since_days=3,
        max_articles_per_query=3,
    )

    results: List[SourceDiagnostic] = []
    for source in build_sources(config, client, [p.ticker for p in profiles]):
        started = time.monotonic()
        try:
            articles = source.fetch(context)
        except Exception as exc:  # noqa: BLE001 - reporting is the point
            source.record_error("unhandled failure", exc)
            articles = []
        outcome = source.outcome(len(articles), time.monotonic() - started)
        results.append(
            SourceDiagnostic(
                source=outcome.name,
                ok=outcome.articles > 0,
                attempted=outcome.attempted,
                succeeded=outcome.succeeded,
                articles=outcome.articles,
                duration_s=outcome.duration_s,
                errors=outcome.errors[:3],
                note="; ".join(outcome.notes[:2]),
            )
        )
    return results


def check_sources(config: Config, profiles: Sequence[CompanyProfile]) -> int:
    """CLI entry point for ``--check-sources``. Returns a shell exit code."""
    results = probe_sources(config, profiles)

    width = max((len(r.source) for r in results), default=10)
    print()
    print("SOURCE VERIFICATION")
    print(f"  {'source'.ljust(width)}  {'status':>8}  {'tried':>5}  {'items':>5}  {'time':>6}  detail")
    print("  " + "-" * (width + 42))
    for result in results:
        status = "VERIFIED" if result.ok else "FAILED"
        detail = (result.errors[0] if result.errors else result.note)[:90]
        print(
            f"  {result.source.ljust(width)}  {status:>8}  {result.attempted:>5}  "
            f"{result.articles:>5}  {result.duration_s:>5.1f}s  {detail}"
        )

    verified = [r.source for r in results if r.ok]
    failed = [r.source for r in results if not r.ok]
    print()
    print(f"  verified: {', '.join(verified) or 'none'}")
    print(f"  failed:   {', '.join(failed) or 'none'}")
    print()
    print("  Record this table in README.md. Do not claim a source works "
          "until it appears as VERIFIED on the machine that will run it.")
    print()
    return 0 if verified else 1
