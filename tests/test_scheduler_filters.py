"""Tests for AlertScheduler filter logic — verify enabled_types gate."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.scheduler import AlertScheduler
from models.db import EventDB
from models.event import Event, EventType, Severity


def make_event(event_type: EventType, severity: Severity = Severity.HIGH) -> Event:
    return Event(
        id=f"evt-{event_type.value}",
        source="test",
        type=event_type,
        title="Test",
        description="Test description",
        lat=38.8097,  # Same as home location below
        lon=-9.2518,
        severity=severity,
        status="active",
        started_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    event_db = EventDB(tmp.name)
    yield event_db
    try:
        os.unlink(tmp.name)
    except PermissionError:
        pass


def base_settings(enabled_types=None, min_severity="LOW"):
    return {
        "location": {"lat": 38.8097, "lon": -9.2518, "radius_km": 5, "name": "Casa"},
        "filters": {
            "min_severity": min_severity,
            "enabled_types": enabled_types,
            "quiet_hours": {"enabled": False, "start": "23:00", "end": "07:00"},
        },
    }


@pytest.mark.asyncio
async def test_enabled_types_none_allows_all_types(db):
    settings = base_settings(enabled_types=None)
    notifier = MagicMock()
    notifier.send_event = AsyncMock(return_value=True)

    scheduler = AlertScheduler(settings=settings, db=db, notifier=notifier)

    collector = MagicMock()
    collector.collect = AsyncMock(return_value=[
        make_event(EventType.FIRE),
        make_event(EventType.ACCIDENT),
        make_event(EventType.CONGESTION),
    ])

    await scheduler._run_collector(collector)

    assert notifier.send_event.await_count == 3


@pytest.mark.asyncio
async def test_enabled_types_filters_disabled_types(db):
    settings = base_settings(enabled_types=["FIRE", "ACCIDENT"])
    notifier = MagicMock()
    notifier.send_event = AsyncMock(return_value=True)

    scheduler = AlertScheduler(settings=settings, db=db, notifier=notifier)

    collector = MagicMock()
    collector.collect = AsyncMock(return_value=[
        make_event(EventType.FIRE),
        make_event(EventType.ACCIDENT),
        make_event(EventType.CONGESTION),  # Should be filtered out
        make_event(EventType.ROADWORK),    # Should be filtered out
    ])

    await scheduler._run_collector(collector)

    assert notifier.send_event.await_count == 2
    sent_types = {call.args[0].type for call in notifier.send_event.await_args_list}
    assert sent_types == {EventType.FIRE, EventType.ACCIDENT}


@pytest.mark.asyncio
async def test_enabled_types_empty_list_blocks_everything(db):
    settings = base_settings(enabled_types=[])
    notifier = MagicMock()
    notifier.send_event = AsyncMock(return_value=True)

    scheduler = AlertScheduler(settings=settings, db=db, notifier=notifier)

    collector = MagicMock()
    collector.collect = AsyncMock(return_value=[
        make_event(EventType.FIRE),
        make_event(EventType.ACCIDENT),
    ])

    await scheduler._run_collector(collector)

    assert notifier.send_event.await_count == 0


@pytest.mark.asyncio
async def test_severity_filter_still_applies_after_type_filter(db):
    settings = base_settings(enabled_types=["FIRE"], min_severity="HIGH")
    notifier = MagicMock()
    notifier.send_event = AsyncMock(return_value=True)

    scheduler = AlertScheduler(settings=settings, db=db, notifier=notifier)

    collector = MagicMock()
    collector.collect = AsyncMock(return_value=[
        make_event(EventType.FIRE, Severity.HIGH),     # passes both
        make_event(EventType.FIRE, Severity.LOW),      # passes type, fails severity
        make_event(EventType.ACCIDENT, Severity.HIGH), # fails type
    ])

    await scheduler._run_collector(collector)

    assert notifier.send_event.await_count == 1
    sent_event = notifier.send_event.await_args_list[0].args[0]
    assert sent_event.type == EventType.FIRE
    assert sent_event.severity == Severity.HIGH


@pytest.mark.asyncio
async def test_in_memory_settings_change_takes_effect_next_run(db):
    """Toggle enabled_types in the live settings dict — next run honors it."""
    settings = base_settings(enabled_types=None)
    notifier = MagicMock()
    notifier.send_event = AsyncMock(return_value=True)

    scheduler = AlertScheduler(settings=settings, db=db, notifier=notifier)

    collector = MagicMock()
    collector.collect = AsyncMock(return_value=[make_event(EventType.CONGESTION)])

    # First run: type allowed
    await scheduler._run_collector(collector)
    assert notifier.send_event.await_count == 1

    # Toggle off CONGESTION in-memory (simulating callback handler)
    settings["filters"]["enabled_types"] = ["FIRE"]

    # Second run with a fresh event id should be filtered now
    collector.collect = AsyncMock(return_value=[
        Event(
            id="evt-CONGESTION-2",
            source="test",
            type=EventType.CONGESTION,
            title="t",
            description="d",
            lat=38.8097,
            lon=-9.2518,
            severity=Severity.HIGH,
            status="active",
            started_at=datetime.now(timezone.utc),
        )
    ])
    await scheduler._run_collector(collector)
    assert notifier.send_event.await_count == 1  # No new sends
