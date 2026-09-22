"""Persistent event database.

Events are stored one JSON file per event under::

    data/events/<year>/<scope>/EVENT-<scope>-<year>-<nnnn>.json

plus a lightweight ``index.json`` so a run can find candidate events to update
without opening thousands of files. Keeping today's events only would make the
historical questions this system is for — *show me every solar tariff event
affecting WAAREEENER* — impossible to answer.

Events evolve. A rumour on day 1, board approval on day 3 and an environmental
clearance on day 20 belong to the same development, so the store looks for an
existing event before creating a new one and records what changed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from .matching import title_similarity
from .models import Event, EventCategory, EventSource, Relationship, utc_now

INDEX_NAME = "index.json"
_EVENT_ID = re.compile(r"^EVENT-(?P<scope>[A-Z0-9]+)-(?P<year>\d{4})-(?P<seq>\d{4})$")


@dataclass
class IndexEntry:
    event_id: str
    path: str
    title: str
    event_date: Optional[str]
    tickers: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    cluster_key: str = ""
    max_impact: int = 0
    last_updated: Optional[str] = None
    source_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "path": self.path,
            "title": self.title,
            "event_date": self.event_date,
            "tickers": self.tickers,
            "categories": self.categories,
            "cluster_key": self.cluster_key,
            "max_impact": self.max_impact,
            "last_updated": self.last_updated,
            "source_count": self.source_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IndexEntry":
        return cls(
            event_id=data["event_id"],
            path=data.get("path", ""),
            title=data.get("title", ""),
            event_date=data.get("event_date"),
            tickers=list(data.get("tickers", [])),
            categories=list(data.get("categories", [])),
            cluster_key=data.get("cluster_key", ""),
            max_impact=int(data.get("max_impact", 0)),
            last_updated=data.get("last_updated"),
            source_count=int(data.get("source_count", 0)),
        )

    @classmethod
    def from_event(cls, event: Event, path: Path, root: Path) -> "IndexEntry":
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            relative = path.as_posix()
        return cls(
            event_id=event.event_id,
            path=relative,
            title=event.title,
            event_date=event.event_date.isoformat() if event.event_date else None,
            tickers=event.tickers,
            categories=[c.value for c in event.event_types],
            cluster_key=event.cluster_key,
            max_impact=event.max_impact,
            last_updated=event.last_updated.isoformat() if event.last_updated else None,
            source_count=event.source_count,
        )


class EventStore:
    """File-backed event database with an in-memory index."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.index: Dict[str, IndexEntry] = {}
        self._loaded = False

    # -- lifecycle -------------------------------------------------------
    def load(self) -> "EventStore":
        self.index = {}
        index_path = self.root / INDEX_NAME
        if index_path.exists():
            try:
                with open(index_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                for entry in payload.get("events", []):
                    item = IndexEntry.from_dict(entry)
                    self.index[item.event_id] = item
            except (json.JSONDecodeError, OSError, KeyError):
                self.index = {}
                self.rebuild_index()
        elif self.root.exists():
            self.rebuild_index()
        self._loaded = True
        return self

    def rebuild_index(self) -> int:
        """Re-derive the index from the stored event files."""
        self.index = {}
        for path in sorted(self.root.rglob("EVENT-*.json")):
            event = self._read(path)
            if event is not None:
                self.index[event.event_id] = IndexEntry.from_event(event, path, self.root)
        return len(self.index)

    def save_index(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": utc_now().isoformat(),
            "count": len(self.index),
            "events": [entry.to_dict() for entry in sorted(
                self.index.values(), key=lambda e: e.event_id
            )],
        }
        path = self.root / INDEX_NAME
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        tmp.replace(path)

    # -- paths -----------------------------------------------------------
    def path_for(self, event: Event) -> Path:
        match = _EVENT_ID.match(event.event_id)
        if match:
            year, scope = match.group("year"), match.group("scope")
        else:
            year = str((event.event_date or date.today()).year)
            scope = (event.tickers[0] if event.tickers else "GLOBAL")
        return self.root / year / scope / f"{event.event_id}.json"

    def next_sequence(self, scope: str, year: int) -> int:
        prefix = f"EVENT-{scope}-{year}-"
        used = [
            int(event_id[len(prefix):])
            for event_id in self.index
            if event_id.startswith(prefix) and event_id[len(prefix):].isdigit()
        ]
        return (max(used) + 1) if used else 1

    # -- read / write ----------------------------------------------------
    def _read(self, path: Path) -> Optional[Event]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return Event.from_dict(json.load(handle))
        except (json.JSONDecodeError, OSError, KeyError, ValueError):
            return None

    def get(self, event_id: str) -> Optional[Event]:
        entry = self.index.get(event_id)
        if entry is None:
            return None
        return self._read(self.root / entry.path)

    def save(self, event: Event) -> Path:
        path = self.path_for(event)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(event.to_dict(), handle, indent=2, ensure_ascii=False)
        tmp.replace(path)
        self.index[event.event_id] = IndexEntry.from_event(event, path, self.root)
        return path

    def save_all(self, events: Iterable[Event]) -> int:
        count = 0
        for event in events:
            self.save(event)
            count += 1
        self.save_index()
        return count

    # -- updates ---------------------------------------------------------
    def find_existing(
        self,
        event: Event,
        window_days: int = 21,
        similarity_threshold: float = 0.55,
    ) -> Optional[Event]:
        """An earlier event that this one is a continuation of, if any.

        Matching on the cluster key alone is too strict — a rumour and its
        confirmation are worded differently — so the title is also compared
        among events touching the same companies inside the window.
        """
        if not event.event_date:
            return None
        tickers = set(event.tickers)
        best: Optional[IndexEntry] = None
        best_score = 0.0

        for entry in self.index.values():
            if entry.event_id == event.event_id:
                continue
            if tickers and not (tickers & set(entry.tickers)):
                continue
            entry_date = _parse_day(entry.event_date)
            if entry_date is None:
                continue
            if abs((event.event_date - entry_date).days) > window_days:
                continue
            if entry.cluster_key and entry.cluster_key == event.cluster_key:
                return self.get(entry.event_id)
            score = title_similarity(event.title, entry.title)
            shared = set(entry.categories) & {c.value for c in event.event_types}
            if shared:
                # The gate (same company, same category, inside the window) is
                # already strong, so a lower title bar is safe here than when
                # clustering a day's articles from scratch.
                score += 0.10
            if score > best_score:
                best, best_score = entry, score

        if best is not None and best_score >= similarity_threshold:
            return self.get(best.event_id)
        return None

    def merge(self, existing: Event, incoming: Event) -> Event:
        """Fold a newly seen event into the one already stored."""
        had_official = existing.has_official_source()
        known = {source.url for source in existing.sources}
        added = [source for source in incoming.sources if source.url not in known]
        if added:
            existing.sources.extend(added)
            existing.record(
                "sources added",
                ", ".join(sorted({s.source_name or s.source_domain for s in added})[:4]),
            )

        new_categories = [c for c in incoming.event_types if c not in existing.event_types]
        if new_categories:
            existing.event_types.extend(new_categories)
            existing.record(
                "categories added", ", ".join(c.value for c in new_categories)
            )

        # An official filing supersedes a press summary as the event's face.
        if incoming.has_official_source() and not had_official:
            existing.title = incoming.title
            existing.primary_source = incoming.primary_source
            existing.record("official confirmation", incoming.primary_source)

        for ticker, impact in incoming.stocks.items():
            previous = existing.stocks.get(ticker)
            if previous is None:
                existing.stocks[ticker] = impact
                existing.record("stock added", f"{ticker} ({impact.relationship.value})")
                continue
            if impact.impact_score > previous.impact_score:
                existing.record(
                    "impact raised",
                    f"{ticker} {previous.impact_score} -> {impact.impact_score}",
                )
                existing.stocks[ticker] = impact
            elif impact.direction != previous.direction:
                existing.record(
                    "direction changed",
                    f"{ticker} {previous.direction.value} -> {impact.direction.value}",
                )
                existing.stocks[ticker] = impact

        if incoming.event_date and existing.event_date:
            existing.event_date = min(existing.event_date, incoming.event_date)
        existing.is_international = existing.is_international or incoming.is_international
        existing.last_updated = utc_now()
        return existing

    # -- research queries ------------------------------------------------
    def query(
        self,
        ticker: Optional[str] = None,
        categories: Optional[Sequence[str]] = None,
        since: Optional[date] = None,
        until: Optional[date] = None,
        min_impact: int = 0,
        limit: int = 200,
    ) -> List[Event]:
        """The historical questions this database exists to answer."""
        wanted = {c.upper() for c in (categories or [])}
        results: List[Event] = []
        entries = sorted(
            self.index.values(),
            key=lambda e: (e.event_date or "", e.event_id),
            reverse=True,
        )
        for entry in entries:
            if ticker and ticker.upper() not in entry.tickers:
                continue
            if wanted and not (wanted & set(entry.categories)):
                continue
            entry_date = _parse_day(entry.event_date)
            if since and (entry_date is None or entry_date < since):
                continue
            if until and (entry_date is None or entry_date > until):
                continue
            if entry.max_impact < min_impact:
                continue
            event = self.get(entry.event_id)
            if event is not None:
                results.append(event)
            if len(results) >= limit:
                break
        return results

    def stats(self) -> Dict[str, Any]:
        by_ticker: Dict[str, int] = {}
        for entry in self.index.values():
            for ticker in entry.tickers:
                by_ticker[ticker] = by_ticker.get(ticker, 0) + 1
        return {
            "events": len(self.index),
            "by_ticker": dict(sorted(by_ticker.items())),
        }


def _parse_day(value: Optional[str]) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
