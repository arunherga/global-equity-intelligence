"""Alerting interface (architecture now, channels later)."""

from __future__ import annotations

from datetime import date

from src.alerts import build_alerts, dispatch_alerts
from src.alerts.base import Alert, ConsoleChannel
from src.config import load_config
from src.models import (
    Direction,
    Event,
    EventCategory,
    EventSource,
    Relationship,
    RunResult,
    SourceType,
    StockImpact,
    utc_now,
)


def make_event(score, official=False):
    event = Event(
        event_id=f"EVENT-WAAREEENER-2026-{score:04d}",
        title="Waaree bags a 1.2 GW export order",
        event_date=date(2026, 9, 22),
        event_types=[EventCategory.EXPORT_ORDER],
        sources=[
            EventSource(
                title="x", url="https://a.test/1", source_name="NSE",
                source_domain="nse.test",
                source_type=(
                    SourceType.COMPANY_EXCHANGE_FILING if official
                    else SourceType.ESTABLISHED_NEWSPAPER
                ),
                is_official=official,
            )
        ],
    )
    event.stocks["WAAREEENER"] = StockImpact(
        ticker="WAAREEENER", relationship=Relationship.DIRECT, impact_score=score,
        direction=Direction.POSITIVE, confidence=0.9,
    )
    return event


def test_only_high_impact_events_alert():
    alerts = build_alerts([make_event(6), make_event(12)], min_impact=11)
    assert len(alerts) == 1
    assert alerts[0].impact_score == 12


def test_an_official_filing_lowers_the_bar():
    alerts = build_alerts([make_event(9, official=True)], min_impact=11)
    assert alerts
    assert "official filing" in alerts[0].reason


def test_alerts_are_off_by_default():
    config = load_config()
    assert config.get("alerts.enabled") is False
    result = RunResult(run_date=date(2026, 9, 22), started_at=utc_now(),
                       events=[make_event(15)])
    assert dispatch_alerts(result, config) == []


def test_enabled_alerts_with_no_channel_still_compute():
    config = load_config(overrides={"alerts": {"enabled": True, "channels": []}})
    result = RunResult(run_date=date(2026, 9, 22), started_at=utc_now(),
                       events=[make_event(14)])
    assert len(dispatch_alerts(result, config)) == 1


def test_console_channel_sends(capsys):
    alert = Alert(ticker="WAAREEENER", event_id="E", title="t", impact_score=12,
                  direction="POSITIVE", relationship="DIRECT", confidence=0.9,
                  reason="impact score 12 >= 11")
    assert ConsoleChannel().send(alert)
    assert "WAAREEENER" in capsys.readouterr().out


def test_a_broken_channel_does_not_break_the_run():
    import src.alerts as alerts_module

    class BrokenChannel:
        name = "broken"

        def send(self, alert):
            raise RuntimeError("no network")

    alerts_module.CHANNELS["broken"] = BrokenChannel
    try:
        config = load_config(overrides={"alerts": {"enabled": True, "channels": ["broken"]}})
        result = RunResult(run_date=date(2026, 9, 22), started_at=utc_now(),
                           events=[make_event(14)])
        assert dispatch_alerts(result, config)  # no exception
    finally:
        alerts_module.CHANNELS.pop("broken", None)


def test_credentials_come_from_the_environment(monkeypatch):
    from src.alerts.base import secret

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert secret("TELEGRAM_BOT_TOKEN") is None
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "abc")
    assert secret("TELEGRAM_BOT_TOKEN") == "abc"
