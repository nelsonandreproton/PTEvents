"""Heartbeat job must fire at startup so a restart doesn't open a >5 min gap
(HetznerCheck raises "sem heartbeat" when next_expected passes)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import bot.scheduler as scheduler_mod
from bot.scheduler import AlertScheduler


@pytest.mark.asyncio
async def test_heartbeat_job_after_start_runs_immediately(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "_HEARTBEAT_AVAILABLE", True)
    settings = {"location": {"lat": 0, "lon": 0, "radius_km": 1, "name": "x"}, "collectors": {}}
    s = AlertScheduler(settings=settings, db=MagicMock(), notifier=MagicMock())
    s.start()
    try:
        job = s._scheduler.get_job("heartbeat")
        assert job is not None
        assert job.next_run_time - datetime.now(timezone.utc) < timedelta(seconds=10)
    finally:
        s.stop()
