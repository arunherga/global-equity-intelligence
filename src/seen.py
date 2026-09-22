"""Seen-article store.

Twice-daily runs overlap by design, so the same article is collected again a
few hours later. The store keeps article ids with the date they were first
seen, so a re-run adds to the day's report rather than repeating it.

Entries expire, because an id kept forever is an id that can never be
re-reported when a story genuinely resurfaces months later.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

from .models import Article


class SeenStore:
    def __init__(self, path: Path, retention_days: int = 45) -> None:
        self.path = Path(path)
        self.retention_days = retention_days
        self._entries: Dict[str, str] = {}
        self._loaded = False

    # -- persistence -----------------------------------------------------
    def load(self) -> "SeenStore":
        self._entries = {}
        if self.path.exists():
            try:
                with open(self.path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    self._entries = {
                        str(k): str(v) for k, v in payload.get("articles", payload).items()
                    }
                elif isinstance(payload, list):  # tolerate an older flat format
                    today = date.today().isoformat()
                    self._entries = {str(k): today for k in payload}
            except (json.JSONDecodeError, OSError, AttributeError):
                # A corrupt store costs one day of dedup, not the run.
                self._entries = {}
        self._loaded = True
        return self

    def save(self) -> None:
        self.prune()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "retention_days": self.retention_days,
            "count": len(self._entries),
            "articles": self._entries,
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        tmp.replace(self.path)

    def prune(self, today: Optional[date] = None) -> int:
        cutoff = (today or date.today()) - timedelta(days=self.retention_days)
        before = len(self._entries)
        self._entries = {
            key: value
            for key, value in self._entries.items()
            if _parse_day(value) is None or _parse_day(value) >= cutoff
        }
        return before - len(self._entries)

    # -- use --------------------------------------------------------------
    def __contains__(self, article_id: str) -> bool:
        return article_id in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def seen_on(self, article_id: str) -> Optional[date]:
        return _parse_day(self._entries.get(article_id, ""))

    def mark(self, article: Article, when: Optional[date] = None) -> None:
        self._entries.setdefault(
            article.article_id, (when or date.today()).isoformat()
        )

    def mark_all(self, articles: Iterable[Article], when: Optional[date] = None) -> None:
        for article in articles:
            self.mark(article, when)

    def split(self, articles: Iterable[Article]) -> tuple[List[Article], List[Article]]:
        """Return ``(fresh, already_seen)`` without mutating the store."""
        fresh: List[Article] = []
        old: List[Article] = []
        for article in articles:
            (old if article.article_id in self._entries else fresh).append(article)
        return fresh, old


def _parse_day(value: str) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return None
