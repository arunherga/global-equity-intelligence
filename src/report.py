"""Daily Markdown intelligence report.

The report is regenerated from the day's full event set rather than appended
to, so the evening run *merges* with the morning's events instead of erasing
them (main.py loads ``data/daily/<date>.json`` before writing).

Section order answers the question the reader actually has at 08:00: what
needs attention, then what the watchlist looks like as a whole, then the
foreign news that never names these companies, then one section per stock.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .config import Config
from .models import (
    BusinessImpact,
    ExposureType,
    Direction,
    Event,
    EventCategory,
    ExposureType,
    Relationship,
    RunResult,
    SourceDiagnostic,
    StockImpact,
    impact_band,
)
from .profiles.loader import CompanyProfile, Watchlist

DIRECTION_SYMBOL = {
    Direction.POSITIVE: "+",
    Direction.NEGATIVE: "-",
    Direction.MIXED: "~",
    Direction.NEUTRAL: "=",
    Direction.UNCERTAIN: "?",
}

BAND_HEADER = {
    "CRITICAL": "CRITICAL",
    "VERY_HIGH": "VERY HIGH IMPACT",
    "HIGH": "HIGH IMPACT",
}

# Per-stock subsections, in the order they appear.
STOCK_SECTIONS: List[Tuple[str, str]] = [
    ("direct", "Direct Company Events"),
    ("industry", "Industry Events"),
    ("competitor", "Competitor Events"),
    ("chain", "Supplier / Customer Events"),
    ("international", "International Events"),
    ("regulatory", "Government / Regulatory Events"),
    ("macro", "Macro Events"),
]


def _fmt_date(value: date) -> str:
    return value.strftime("%d %B %Y").lstrip("0")


def _impact_for(event: Event, ticker: str) -> Optional[StockImpact]:
    return event.stocks.get(ticker)


def section_for(event: Event, impact: StockImpact) -> str:
    """Which per-stock subsection an event belongs in."""
    if impact.relationship == Relationship.DIRECT:
        return "direct"
    types = {e.exposure_type for e in impact.exposures}
    categories = set(event.event_types)

    if event.is_international or impact.relationship == Relationship.MACRO and event.is_international:
        if ExposureType.COMPETITOR in types:
            return "competitor"
        return "international"
    if categories & {
        EventCategory.REGULATORY, EventCategory.GOVERNMENT_POLICY, EventCategory.TARIFF,
        EventCategory.TRADE_POLICY, EventCategory.EXPORT_RESTRICTION,
        EventCategory.IMPORT_RESTRICTION,
    } or ExposureType.REGULATION in types:
        return "regulatory"
    if ExposureType.COMPETITOR in types:
        return "competitor"
    if types & {ExposureType.SUPPLIER, ExposureType.CUSTOMER}:
        return "chain"
    if impact.relationship == Relationship.MACRO:
        return "macro"
    return "industry"


class ReportBuilder:
    def __init__(self, config: Config, watchlist: Watchlist) -> None:
        self.config = config
        self.watchlist = watchlist
        self.min_impact = int(config.get("scoring.report_min_impact", 5))
        self.high = int(config.get("scoring.high_impact_threshold", 8))
        self.critical = int(config.get("scoring.critical_threshold", 13))
        self.weak_min = int(config.get("scoring.weak_relationship_min_impact", 9))
        self.max_per_section = int(config.get("report.max_events_per_section", 8))
        self.max_watch = int(config.get("report.max_watch_items", 5))
        self.show_diagnostics = bool(config.get("report.show_diagnostics", True))
        self.theme_collapse_min = int(config.get("report.theme_collapse_min", 3))
        # Event ids already printed in full, so later sections can point at
        # them instead of repeating them.
        self._detailed: Set[str] = set()

    # -- filtering -------------------------------------------------------
    def is_reportable(self, event: Event, impact: StockImpact) -> bool:
        if impact.relationship == Relationship.WEAK:
            return impact.impact_score >= self.weak_min
        return impact.impact_score >= self.min_impact

    def reportable_pairs(self, events: Sequence[Event]) -> List[Tuple[Event, StockImpact]]:
        pairs: List[Tuple[Event, StockImpact]] = []
        for event in events:
            for impact in event.stocks.values():
                if self.is_reportable(event, impact):
                    pairs.append((event, impact))
        pairs.sort(key=lambda p: (-p[1].impact_score, -p[1].confidence, p[0].event_id))
        return pairs

    # -- building --------------------------------------------------------
    def build(self, result: RunResult) -> str:
        self._detailed = set()
        events = list(result.events)
        pairs = self.reportable_pairs(events)

        lines: List[str] = []
        lines.extend(self._header(result, events, pairs))
        lines.extend(self._attention(pairs))
        lines.extend(self._dashboard(events))
        lines.extend(self._global_section(events))
        lines.extend(self._cross_stock(events))
        lines.extend(self._per_stock(events))
        if self.show_diagnostics:
            lines.extend(self._diagnostics(result))
        lines.extend(self._footer())
        return "\n".join(lines).rstrip() + "\n"

    # -- sections --------------------------------------------------------
    def _header(
        self,
        result: RunResult,
        events: Sequence[Event],
        pairs: Sequence[Tuple[Event, StockImpact]],
    ) -> List[str]:
        stats = result.stats
        high = sum(1 for _, i in pairs if i.impact_score >= self.high)
        critical = sum(1 for _, i in pairs if i.impact_score >= self.critical)
        international = sum(1 for e in events if e.is_international)
        official = sum(1 for e in events if e.has_official_source())
        relevant_events = len({e.event_id for e, _ in pairs})

        return [
            "# GLOBAL EQUITY INTELLIGENCE",
            "",
            f"**{_fmt_date(result.run_date)}**",
            "",
            f"Stocks monitored: **{len(result.tickers) or len(self.watchlist)}**",
            "",
            "| | |",
            "| --- | ---: |",
            f"| Articles scanned | {stats.articles_scanned:,} |",
            f"| Unique articles | {stats.unique_articles:,} |",
            f"| Events detected | {len(events):,} |",
            f"| Relevant events | {relevant_events:,} |",
            f"| High-impact events | {high:,} |",
            f"| Critical events | {critical:,} |",
            f"| International events affecting the watchlist | {international:,} |",
            f"| Official company / exchange announcements | {official:,} |",
            "",
            "Impact is scored 0-15 and is **not** a view on the share price. "
            "Direction is recorded separately and is often uncertain.",
            "",
        ]

    def _attention(self, pairs: Sequence[Tuple[Event, StockImpact]]) -> List[str]:
        urgent = [(e, i) for e, i in pairs if i.impact_score >= self.high]
        lines = ["## What needs attention", ""]
        if not urgent:
            lines += [
                "Nothing crossed the high-impact threshold "
                f"({self.high}/15) in this run.",
                "",
            ]
            return lines

        # One block per EVENT. Iterating the (event, stock) pairs printed the
        # whole ~40-line block once per affected company, so a single RBI
        # decision appeared twice in full - the loudest repetition in the
        # report.
        grouped: Dict[str, List[StockImpact]] = {}
        order: List[Event] = []
        for event, impact in urgent:
            if event.event_id not in grouped:
                grouped[event.event_id] = []
                order.append(event)
            grouped[event.event_id].append(impact)

        current_band = ""
        for event in order[: self.max_per_section * 2]:
            impacts = sorted(grouped[event.event_id], key=lambda i: -i.impact_score)
            band = impact_band(impacts[0].impact_score)
            if band != current_band:
                current_band = band
                lines += [f"### {BAND_HEADER.get(band, band)}", ""]
            lines.extend(self._event_block(event, impacts))
            self._detailed.add(event.event_id)
        return lines

    def _event_block(self, event: Event, impacts: Sequence[StockImpact]) -> List[str]:
        impact = impacts[0]
        profile = self.watchlist.get(impact.ticker)
        heading = f"#### {impact.ticker} — {profile.company}"
        if len(impacts) > 1:
            others = ", ".join(i.ticker for i in impacts[1:])
            heading += f"  ·  also affects {others}"
        lines = [
            heading,
            "",
            f"**Impact** {impact.impact_score}/15 &nbsp;&nbsp; "
            f"**Direction** {impact.direction.value} &nbsp;&nbsp; "
            f"**Confidence** {int(round(impact.confidence * 100))}% &nbsp;&nbsp; "
            f"**Relationship** {impact.relationship.value}",
            "",
            f"**Event** — {event.title}",
            "",
        ]
        if len(impacts) > 1:
            lines += ["**Also affects**", ""]
            for other in impacts[1:]:
                lines.append(
                    f"- **{other.ticker}** — {other.relationship.value}, "
                    f"{other.impact_score}/15, {other.direction.value}"
                )
            lines.append("")
        if event.summary:
            lines += [f"> {event.summary[:400]}", ""]

        lines += ["**Why it matters**", "", impact.why_it_matters or "—", ""]

        if impact.business_impacts:
            lines += [
                "**Could affect**",
                "",
                *[f"- {b.value.replace('_', ' ').title()}" for b in impact.business_impacts],
                "",
            ]

        lines += [f"**Time horizon** {impact.time_horizon.value.replace('_', ' ').title()}", ""]

        if impact.score_reasons:
            lines += [
                "**Score reasons**",
                "",
                *[f"- {r}" for r in impact.score_reasons],
                "",
            ]

        if impact.watch_next:
            lines += [
                "**Watch next**",
                "",
                *[f"- {w}" for w in impact.watch_next[: self.max_watch]],
                "",
            ]

        if impact.ai_analysis:
            lines += self._ai_block(impact.ai_analysis)

        lines += ["**Sources**", ""]
        for source in event.sources[:6]:
            label = source.source_name or source.source_domain or "source"
            official = " *(official)*" if source.is_official else ""
            lines.append(f"- [{label}]({source.url}){official}")
        lines += ["", f"`{event.event_id}`", "", "---", ""]
        return lines

    def _ai_block(self, analysis: Dict[str, object]) -> List[str]:
        """AI commentary, clearly labelled as such."""
        fields = [
            ("revenue_effect", "Revenue"),
            ("margin_effect", "Margin"),
            ("cost_effect", "Costs"),
            ("competitive_effect", "Competition"),
            ("regulatory_effect", "Regulation"),
            ("short_term", "Short term"),
            ("medium_long_term", "Medium / long term"),
            ("second_order_effects", "Second-order effects"),
            ("key_uncertainty", "Key uncertainty"),
        ]
        provider = analysis.get("provider", "ai")
        model = analysis.get("model", "")
        lines = [f"**AI analysis** _(optional layer, {provider} {model})_".rstrip() + "", ""]
        for key, label in fields:
            value = str(analysis.get(key, "")).strip()
            if value:
                lines.append(f"- **{label}** — {value}")
        monitor = analysis.get("monitor_next") or []
        if isinstance(monitor, list) and monitor:
            lines.append(f"- **Monitor next** — {'; '.join(str(m) for m in monitor[:5])}")
        lines.append("")
        return lines

    def _dashboard(self, events: Sequence[Event]) -> List[str]:
        counts: Dict[str, Counter] = defaultdict(Counter)
        for event in events:
            for ticker, impact in event.stocks.items():
                if not self.is_reportable(event, impact):
                    continue
                counts[ticker]["events"] += 1
                if impact.impact_score >= self.high:
                    counts[ticker]["high"] += 1
                counts[ticker][impact.direction.value] += 1

        lines = [
            "## Watchlist dashboard",
            "",
            "Event counts, not a ranking. This is not a buy or sell list.",
            "",
            "| Ticker | Events | High impact | + | - | Mixed | ? |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
        for profile in self.watchlist.profiles:
            row = counts.get(profile.ticker, Counter())
            lines.append(
                f"| {profile.ticker} | {row['events']} | {row['high']} | "
                f"{row[Direction.POSITIVE.value]} | {row[Direction.NEGATIVE.value]} | "
                f"{row[Direction.MIXED.value]} | "
                f"{row[Direction.UNCERTAIN.value] + row[Direction.NEUTRAL.value]} |"
            )
        lines.append("")
        return lines

    def _global_section(self, events: Sequence[Event]) -> List[str]:
        # Deliberately excludes DIRECT events: a company's own announcement
        # belongs in its own section. This section is for the foreign news
        # that never names these companies, which is the whole point.
        rows: List[Tuple[Event, StockImpact]] = []
        for event in events:
            if not event.is_international:
                continue
            for impact in event.stocks.values():
                if impact.relationship == Relationship.DIRECT:
                    continue
                if self.is_reportable(event, impact):
                    rows.append((event, impact))
        rows.sort(key=lambda p: -p[1].impact_score)

        lines = ["## Global events affecting my stocks", ""]
        if not rows:
            lines += ["No international developments cleared the threshold in this run.", ""]
            return lines

        lines += [
            "Foreign and cross-border news that may never mention these companies "
            "by name. A company's own announcements are in its own section below.",
            "",
        ]
        seen: Dict[str, List[str]] = {}
        order: List[Tuple[str, Event]] = []
        for event, impact in rows:
            if event.event_id not in seen:
                seen[event.event_id] = []
                order.append((event.event_id, event))
            seen[event.event_id].append(
                f"{impact.ticker} ({impact.relationship.value}, "
                f"{impact.impact_score}/15, {impact.direction.value})"
            )
        for event_id, event in order[: self.max_per_section * 2]:
            lines.append(f"**{event.title}**")
            lines.append("")
            for row in seen[event_id]:
                lines.append(f"- → {row}")
            lines.append("")
        return lines

    def _cross_stock(self, events: Sequence[Event]) -> List[str]:
        multi = [
            e for e in events
            if len([i for i in e.stocks.values() if self.is_reportable(e, i)]) > 1
        ]
        multi.sort(key=lambda e: -e.max_impact)
        lines = ["## Cross-stock events", ""]
        if not multi:
            lines += ["No single development touched more than one watchlist company.", ""]
            return lines
        lines += ["One development, several holdings. Stored as one event.", ""]
        for event in multi[: self.max_per_section]:
            lines += [f"**{event.title}**", ""]
            for impact in sorted(event.stocks.values(), key=lambda i: -i.impact_score):
                if not self.is_reportable(event, impact):
                    continue
                affected = ", ".join(
                    b.value.replace("_", " ").lower() for b in impact.business_impacts[:3]
                ) or "not specified"
                lines.append(
                    f"- **{impact.ticker}** — {impact.relationship.value}, "
                    f"{impact.impact_score}/15, {impact.direction.value}; "
                    f"could affect {affected}"
                )
            lines += ["", f"`{event.event_id}`", ""]
        return lines

    def _per_stock(self, events: Sequence[Event]) -> List[str]:
        lines: List[str] = []
        for profile in self.watchlist.profiles:
            pairs = [
                (e, i)
                for e in events
                for i in [_impact_for(e, profile.ticker)]
                if i is not None and self.is_reportable(e, i)
            ]
            lines += [f"## {profile.ticker} Intelligence", ""]
            if not pairs:
                lines += [f"_No qualifying events for {profile.company} in this run._", ""]
                continue

            pairs.sort(key=lambda p: (-p[1].impact_score, -p[1].confidence))
            grouped: Dict[str, List[Tuple[Event, StockImpact]]] = defaultdict(list)
            for event, impact in pairs:
                grouped[section_for(event, impact)].append((event, impact))

            for key, heading in STOCK_SECTIONS:
                rows = grouped.get(key)
                if not rows:
                    continue  # only display sections that contain useful events
                lines += [f"### {heading}", ""]
                for event, impact, similar in self.collapse_themes(rows)[
                    : self.max_per_section
                ]:
                    lines.append(self._one_liner(event, impact, similar))
                lines.append("")

            watch = self._watch_items(pairs)
            if watch:
                lines += ["### Things to Watch", "", *[f"- {w}" for w in watch], ""]
        return lines


    # -- theme collapsing -------------------------------------------------
    @staticmethod
    def theme_key(impact: StockImpact) -> Optional[str]:
        """The commodity or industry topic an event is really about.

        The live backfill produced twelve separate diesel-price events in one
        run - US farmers, Ontario farmers, Australia, a refinery crunch, an
        export ban - all genuinely different articles, all the same story for
        a reader holding JKIPL. Clustering correctly kept them apart as
        events; the report is the right place to gather them up.
        """
        for wanted in (ExposureType.COMMODITY, ExposureType.INDUSTRY):
            for exposure in impact.exposures:
                if exposure.exposure_type is wanted:
                    return f"{wanted.value}:{exposure.term.lower()}"
        return None

    def collapse_themes(
        self, rows: Sequence[Tuple[Event, StockImpact]]
    ) -> List[Tuple[Event, StockImpact, int]]:
        """Fold same-topic events into their strongest example plus a count."""
        groups: Dict[str, List[Tuple[Event, StockImpact]]] = {}
        order: List[str] = []
        ungrouped: List[Tuple[Event, StockImpact, int]] = []

        for event, impact in rows:
            key = self.theme_key(impact)
            if key is None:
                ungrouped.append((event, impact, 0))
                continue
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append((event, impact))

        out: List[Tuple[Event, StockImpact, int]] = list(ungrouped)
        for key in order:
            members = sorted(groups[key], key=lambda p: -p[1].impact_score)
            if len(members) >= self.theme_collapse_min:
                out.append((members[0][0], members[0][1], len(members) - 1))
            else:
                out.extend((e, i, 0) for e, i in members)

        out.sort(key=lambda t: (-t[1].impact_score, t[0].event_id))
        return out

    def _one_liner(self, event: Event, impact: StockImpact, similar: int = 0) -> str:
        symbol = DIRECTION_SYMBOL.get(impact.direction, "?")
        sources = f"{event.article_count} source" + ("s" if event.article_count != 1 else "")
        official = ", official" if event.has_official_source() else ""
        note = ""
        if similar:
            note += f" _+{similar} similar report" + ("s" if similar != 1 else "") + "_"
        if event.event_id in self._detailed:
            note += " _↑ detailed above_"
        return (
            f"- **[{impact.impact_score}/15 {symbol}]** {event.title} "
            f"_({impact.relationship.value}, {impact.direction.value}, "
            f"{int(round(impact.confidence * 100))}% confidence, {sources}{official})_"
            f"{note} `{event.event_id}`"
        )

    def _watch_items(self, pairs: Sequence[Tuple[Event, StockImpact]]) -> List[str]:
        seen: List[str] = []
        for _, impact in pairs:
            for item in impact.watch_next:
                if item not in seen:
                    seen.append(item)
            if len(seen) >= self.max_watch:
                break
        return seen[: self.max_watch]

    def _diagnostics(self, result: RunResult) -> List[str]:
        lines = [
            "## Run diagnostics",
            "",
            "A source is only described as working when it actually returned items "
            "in this run. One source failing never stops the others.",
            "",
            "| Source | Status | Attempted | Articles | Time | Notes |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
        for diagnostic in result.diagnostics:
            status = "ok" if diagnostic.ok else "FAILED"
            note = (diagnostic.errors[0] if diagnostic.errors else diagnostic.note) or ""
            note = note.replace("|", "/")[:120]
            lines.append(
                f"| {diagnostic.source} | {status} | {diagnostic.attempted} | "
                f"{diagnostic.articles} | {diagnostic.duration_s:.1f}s | {note} |"
            )
        lines.append("")
        failed = [d for d in result.diagnostics if not d.ok]
        if failed:
            lines += [
                "Failed sources in this run: "
                + ", ".join(sorted(d.source for d in failed))
                + ".",
                "",
            ]
        return lines

    def _footer(self) -> List[str]:
        return [
            "---",
            "",
            "_Generated by global-equity-intelligence. Impact scores measure how much "
            "attention a development deserves, not whether a stock is worth owning. "
            "Nothing here is investment advice._",
            "",
        ]


def build_report(result: RunResult, config: Config, watchlist: Watchlist) -> str:
    return ReportBuilder(config, watchlist).build(result)


def report_path(config: Config, run_date: date, subdir: str = "") -> "object":
    directory = config.reports_dir / subdir if subdir else config.reports_dir
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{run_date.isoformat()}.md"


def write_report(content: str, path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
