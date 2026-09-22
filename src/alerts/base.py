"""Alert interface.

Immediate alerting is a later feature, but the seam belongs in the codebase
now so that adding Telegram, email, Slack or a mobile push later is one module
and one config line — not a refactor.

V1 ships the console channel only. Nothing here sends anything anywhere unless
``alerts.enabled`` is true and a channel is configured.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Sequence

from ..models import Event, StockImpact


@dataclass
class Alert:
    """One thing worth interrupting someone for."""

    ticker: str
    event_id: str
    title: str
    impact_score: int
    direction: str
    relationship: str
    confidence: float
    reason: str
    url: str = ""
    sources: List[str] = field(default_factory=list)

    def as_text(self) -> str:
        return (
            f"[{self.ticker}] {self.impact_score}/15 {self.direction} "
            f"({self.relationship}, {int(round(self.confidence * 100))}% confidence)\n"
            f"{self.title}\n"
            f"Why: {self.reason}\n"
            f"{self.url}".strip()
        )

    def to_dict(self) -> Dict[str, object]:
        return {
            "ticker": self.ticker,
            "event_id": self.event_id,
            "title": self.title,
            "impact_score": self.impact_score,
            "direction": self.direction,
            "relationship": self.relationship,
            "confidence": self.confidence,
            "reason": self.reason,
            "url": self.url,
            "sources": list(self.sources),
        }


class AlertChannel(Protocol):  # pragma: no cover - interface
    name: str

    def send(self, alert: Alert) -> bool:
        """Deliver one alert. Must never raise; return False on failure."""


class ConsoleChannel:
    """The only channel implemented in V1. Deliberately trivial."""

    name = "console"

    def send(self, alert: Alert) -> bool:
        print("\n=== ALERT ===")
        print(alert.as_text())
        return True


def secret(name: str) -> Optional[str]:
    """Credentials come from the environment, never from a config file."""
    value = os.environ.get(name, "").strip()
    return value or None


def build_alerts(
    events: Sequence[Event],
    min_impact: int,
    alert_on_official_filing: bool = True,
) -> List[Alert]:
    """Which events deserve an interruption."""
    alerts: List[Alert] = []
    for event in events:
        official = event.has_official_source()
        for impact in event.stocks.values():
            trigger = ""
            if impact.impact_score >= min_impact:
                trigger = f"impact score {impact.impact_score} >= {min_impact}"
            elif alert_on_official_filing and official and impact.impact_score >= min_impact - 3:
                trigger = "official filing on a material development"
            if not trigger:
                continue
            alerts.append(
                Alert(
                    ticker=impact.ticker,
                    event_id=event.event_id,
                    title=event.title,
                    impact_score=impact.impact_score,
                    direction=impact.direction.value,
                    relationship=impact.relationship.value,
                    confidence=impact.confidence,
                    reason=trigger,
                    url=event.sources[0].url if event.sources else "",
                    sources=[s.source_name for s in event.sources[:3]],
                )
            )
    alerts.sort(key=lambda a: -a.impact_score)
    return alerts
