"""Collector failures are logged only — never sent to Telegram."""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.scheduler import AlertScheduler
from collectors.base import SourceUnavailableError

SETTINGS = {
    "location": {"lat": 38.8097, "lon": -9.2518, "radius_km": 5, "name": "Casa"},
    "filters": {"quiet_hours": {"enabled": False}},
}


class FakeCollector:
    def __init__(self, collect):
        self.collect = collect


def make_scheduler():
    notifier = MagicMock()
    notifier.send_event = AsyncMock(return_value=True)
    notifier.send_text = AsyncMock(return_value=True)
    return AlertScheduler(settings=SETTINGS, db=MagicMock(), notifier=notifier), notifier


@pytest.mark.asyncio
async def test_run_collector_repeated_failures_then_recovery_sends_no_telegram():
    sched, notifier = make_scheduler()
    c = FakeCollector(AsyncMock(side_effect=SourceUnavailableError("all down")))
    for _ in range(10):
        await sched._run_collector(c)
    c.collect = AsyncMock(return_value=[])
    await sched._run_collector(c)
    notifier.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_run_collector_source_unavailable_logs_warning(caplog):
    sched, _ = make_scheduler()
    c = FakeCollector(AsyncMock(side_effect=SourceUnavailableError("waze: 403")))
    with caplog.at_level(logging.WARNING, logger="bot.scheduler"):
        await sched._run_collector(c)
    assert any("waze: 403" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_run_collector_unexpected_error_does_not_crash_job():
    sched, notifier = make_scheduler()
    c = FakeCollector(AsyncMock(side_effect=RuntimeError("boom")))
    await sched._run_collector(c)
    notifier.send_text.assert_not_called()
