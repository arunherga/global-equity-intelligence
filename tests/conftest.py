"""Shared fixtures. Every test in this suite runs offline."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_config                       # noqa: E402
from src.models import Article, SourceType               # noqa: E402
from src.profiles import load_watchlist                  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def config():
    return load_config()


@pytest.fixture(scope="session")
def watchlist(config):
    return load_watchlist(config=config)


@pytest.fixture
def now():
    return datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def make_article(now):
    def _make(
        title: str,
        *,
        url: str = "",
        domain: str = "example.test",
        name: str = "Example",
        source_type: SourceType = SourceType.UNKNOWN_NEWS_SITE,
        summary: str = "",
        official: bool = False,
        hours_ago: int = 3,
    ) -> Article:
        return Article(
            title=title,
            url=url or f"https://{domain}/{abs(hash(title)) % 10**8}",
            source_domain=domain,
            source_name=name,
            source_type=source_type,
            summary=summary,
            published=now - timedelta(hours=hours_ago),
            is_official=official,
        )

    return _make


@pytest.fixture(scope="session")
def fixture_articles():
    from scripts.generate_sample_report import load_fixture_articles

    return load_fixture_articles(now=datetime(2026, 9, 22, 6, 0, tzinfo=timezone.utc))
