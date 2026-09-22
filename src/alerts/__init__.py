"""Alert dispatch.

Channels are registered here. Adding Telegram means writing a class with a
``send`` method and adding one entry to :data:`CHANNELS` — no other change.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from ..config import Config
from ..models import RunResult
from .base import Alert, AlertChannel, ConsoleChannel, build_alerts, secret

LOG = logging.getLogger("gei.alerts")

# name -> factory. Future: telegram, email, slack, discord, mobile push.
CHANNELS: Dict[str, type] = {
    "console": ConsoleChannel,
}

__all__ = ["Alert", "AlertChannel", "ConsoleChannel", "build_alerts", "dispatch_alerts", "secret"]


def dispatch_alerts(result: RunResult, config: Config) -> List[Alert]:
    """Send alerts for the run, if alerting is enabled and configured."""
    if not config.get("alerts.enabled", False):
        return []

    alerts = build_alerts(
        result.events,
        min_impact=int(config.get("alerts.min_impact_score", 11)),
        alert_on_official_filing=bool(config.get("alerts.alert_on_official_filing", True)),
    )
    if not alerts:
        return []

    names = [str(c) for c in config.get("alerts.channels", []) or []]
    if not names:
        LOG.info("%d alert(s) triggered but no channels configured", len(alerts))
        return alerts

    for name in names:
        factory = CHANNELS.get(name)
        if factory is None:
            LOG.warning("unknown alert channel %r; skipped", name)
            continue
        channel = factory()
        for alert in alerts:
            try:
                channel.send(alert)
            except Exception as exc:  # noqa: BLE001 - alerting must never break a run
                LOG.warning("alert channel %s failed: %s", name, exc)
    return alerts
