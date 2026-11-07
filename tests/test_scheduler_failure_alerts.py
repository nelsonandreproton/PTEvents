"""Tests for consecutive-failure Telegram alerts in AlertScheduler._run_collector."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.scheduler import FAILURE_ALERT_THRESHOLD, AlertScheduler

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


def failing(msg="boom"):
    return FakeCollector(AsyncMock(side_effect=RuntimeError(msg)))


def succeeding():
    return FakeCollector(AsyncMock(return_value=[]))


@pytest.mark.asyncio
async def test_no_alert_below_threshold():
    sched, notifier = make_scheduler()
    c = failing()
    for _ in range(FAILURE_ALERT_THRESHOLD - 1):
        await sched._run_collector(c)
    notifier.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_exactly_one_alert_at_threshold_and_none_after():
    sched, notifier = make_scheduler()
    c = failing("it broke")
    for _ in range(FAILURE_ALERT_THRESHOLD + 3):
        await sched._run_collector(c)
    assert notifier.send_text.await_count == 1
    text = notifier.send_text.await_args.args[0]
    assert "FakeCollector" in text
    assert "indispon" in text
    assert "it broke" in text


@pytest.mark.asyncio
async def test_alert_escapes_markdown_and_truncates_error():
    sched, notifier = make_scheduler()
    c = failing("a.b_c" + "x" * 500)
    for _ in range(FAILURE_ALERT_THRESHOLD):
        await sched._run_collector(c)
    text = notifier.send_text.await_args.args[0]
    assert r"a\.b\_c" in text
    assert len(text) < 400


@pytest.mark.asyncio
async def test_recovery_message_sent_once_after_alert():
    sched, notifier = make_scheduler()
    bad = failing()
    for _ in range(FAILURE_ALERT_THRESHOLD):
        await sched._run_collector(bad)
    bad.collect = AsyncMock(return_value=[])
    await sched._run_collector(bad)
    await sched._run_collector(bad)
    assert notifier.send_text.await_count == 2
    assert "recuperada" in notifier.send_text.await_args.args[0]


@pytest.mark.asyncio
async def test_success_without_prior_alert_sends_nothing():
    sched, notifier = make_scheduler()
    await sched._run_collector(succeeding())
    notifier.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_success_resets_counter():
    sched, notifier = make_scheduler()
    c = failing()
    for _ in range(FAILURE_ALERT_THRESHOLD - 1):
        await sched._run_collector(c)
    c.collect = AsyncMock(return_value=[])
    await sched._run_collector(c)
    c.collect = AsyncMock(side_effect=RuntimeError("again"))
    for _ in range(FAILURE_ALERT_THRESHOLD - 1):
        await sched._run_collector(c)
    notifier.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_send_text_failure_does_not_crash_job():
    sched, notifier = make_scheduler()
    notifier.send_text = AsyncMock(side_effect=RuntimeError("telegram down"))
    c = failing()
    for _ in range(FAILURE_ALERT_THRESHOLD):
        await sched._run_collector(c)
    c.collect = AsyncMock(return_value=[])
    await sched._run_collector(c)
