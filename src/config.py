"""Configuration loading. Everything tunable lives in config.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .models import SourceType

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config.yaml"
DEFAULT_WATCHLIST_PATH = REPO_ROOT / "watchlist.yaml"


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


@dataclass
class FeedConfig:
    name: str
    url: str
    source_type: SourceType = SourceType.UNKNOWN_NEWS_SITE
    tickers: List[str] = field(default_factory=list)
    group: str = "general"


@dataclass
class Config:
    """Typed view over config.yaml with sane fallbacks."""

    raw: Dict[str, Any] = field(default_factory=dict)
    root: Path = REPO_ROOT
    path: Optional[Path] = None

    # -- generic access --------------------------------------------------
    def section(self, name: str) -> Dict[str, Any]:
        value = self.raw.get(name)
        return value if isinstance(value, dict) else {}

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # -- frequently used -------------------------------------------------
    @property
    def lookback_hours(self) -> int:
        return int(self.get("run.lookback_hours", 36))

    @property
    def max_queries_per_ticker(self) -> int:
        return int(self.get("run.max_queries_per_ticker", 25))

    @property
    def max_articles_per_query(self) -> int:
        return int(self.get("run.max_articles_per_query", 25))

    @property
    def http(self) -> Dict[str, Any]:
        return self.section("http")

    @property
    def source_quality(self) -> Dict[str, int]:
        return {str(k): int(v) for k, v in self.section("source_quality").items()}

    @property
    def source_domains(self) -> Dict[str, str]:
        return {str(k).lower(): str(v) for k, v in self.section("source_domains").items()}

    def source_enabled(self, name: str) -> bool:
        return bool(self.get(f"sources.{name}.enabled", False))

    def quality_for(self, source_type: SourceType) -> int:
        return self.source_quality.get(source_type.value, 3)

    def source_type_for_domain(self, domain: str) -> SourceType:
        domain = (domain or "").lower().lstrip(".")
        mapping = self.source_domains
        if domain in mapping:
            return SourceType(mapping[domain])
        parts = domain.split(".")
        for i in range(1, len(parts) - 1):
            candidate = ".".join(parts[i:])
            if candidate in mapping:
                return SourceType(mapping[candidate])
        return SourceType.UNKNOWN_NEWS_SITE

    def feeds(self, tickers: Optional[List[str]] = None) -> List[FeedConfig]:
        """Configured RSS feeds, optionally filtered to relevant tickers."""
        result: List[FeedConfig] = []
        wanted = set(tickers or [])
        for group, entries in self.section("feeds").items():
            for entry in entries or []:
                feed_tickers = [str(t) for t in entry.get("tickers", [])]
                if wanted and feed_tickers and not (set(feed_tickers) & wanted):
                    continue
                try:
                    stype = SourceType(entry.get("source_type", "unknown_news_site"))
                except ValueError:
                    stype = SourceType.UNKNOWN_NEWS_SITE
                result.append(
                    FeedConfig(
                        name=entry.get("name", entry.get("url", "feed")),
                        url=entry["url"],
                        source_type=stype,
                        tickers=feed_tickers,
                        group=group,
                    )
                )
        return result

    # -- paths -----------------------------------------------------------
    def storage_path(self, key: str) -> Path:
        default = {
            "events_dir": "data/events",
            "daily_dir": "data/daily",
            "profiles_dir": "data/profiles",
            "backfill_dir": "data/backfill",
            "seen_file": "data/seen_articles.json",
        }[key]
        return self.root / str(self.get(f"storage.{key}", default))

    @property
    def reports_dir(self) -> Path:
        return self.root / str(self.get("report.directory", "reports"))

    # -- ai --------------------------------------------------------------
    @property
    def ai_enabled(self) -> bool:
        if os.environ.get("GEI_AI_ENABLED", "").lower() in {"1", "true", "yes"}:
            return True
        return bool(self.get("ai.enabled", False))


def load_config(
    path: Optional[Path] = None,
    overrides: Optional[Dict[str, Any]] = None,
    root: Optional[Path] = None,
) -> Config:
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    data: Dict[str, Any] = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    if overrides:
        data = _deep_merge(data, overrides)
    return Config(raw=data, root=Path(root) if root else REPO_ROOT, path=config_path)
