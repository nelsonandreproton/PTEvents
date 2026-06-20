"""Integration test for the filter-persistence chain.

Proves the deploy-survival fix: a `PUT /ptevents/api/filters` writes overrides
to the data-volume prefs file (NOT settings.yaml), and a subsequent startup
reconstructs the live filters from base + overrides. This is the regression
guard for "Telegram selections lost on every deploy.sh".
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from bot.preferences import apply_overrides, load_filter_overrides
from bot.web import _build_app


class _FakeDB:
    def get_active(self, limit):
        return []

    def get_active_full(self, limit):
        return []


@pytest.fixture
def prefs_path():
    tmpdir = tempfile.mkdtemp()
    path = Path(tmpdir) / "filter_prefs.yaml"
    yield path
    for p in (path,):
        try:
            if p.exists():
                os.unlink(p)
        except OSError:
            pass
    try:
        os.rmdir(tmpdir)
    except OSError:
        pass


def _base_settings():
    return {
        "location": {"lat": 38.8, "lon": -9.2, "radius_km": 5, "name": "Casa"},
        "filters": {
            "min_severity": "LOW",
            "enabled_types": None,
            "quiet_hours": {"enabled": True, "start": "23:00", "end": "07:00"},
        },
    }


@pytest.mark.asyncio
async def test_put_filters_persists_overrides_and_survives_reload(prefs_path):
    settings = _base_settings()
    app = _build_app(_FakeDB(), settings, prefs_path=prefs_path)

    async with TestClient(TestServer(app)) as client:
        resp = await client.put(
            "/ptevents/api/filters",
            json={"min_severity": "HIGH", "enabled_types": ["FIRE", "STORM"]},
        )
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True

    # In-memory dict mutated immediately (scheduler picks this up next tick).
    assert settings["filters"]["min_severity"] == "HIGH"
    assert settings["filters"]["enabled_types"] == ["FIRE", "STORM"]

    # Persisted to the prefs file in the data volume.
    assert prefs_path.exists()
    overrides = load_filter_overrides(prefs_path)
    assert overrides == {"min_severity": "HIGH", "enabled_types": ["FIRE", "STORM"]}

    # Simulate a fresh startup AFTER a deploy: base reverts to tracked defaults,
    # overrides are re-applied from the volume → selections survive.
    fresh_base = _base_settings()["filters"]
    reloaded = apply_overrides(fresh_base, load_filter_overrides(prefs_path))
    assert reloaded["min_severity"] == "HIGH"
    assert reloaded["enabled_types"] == ["FIRE", "STORM"]
    # quiet_hours still comes from the base, not the overrides file.
    assert reloaded["quiet_hours"] == {"enabled": True, "start": "23:00", "end": "07:00"}


@pytest.mark.asyncio
async def test_get_filters_reflects_current_state(prefs_path):
    settings = _base_settings()
    app = _build_app(_FakeDB(), settings, prefs_path=prefs_path)

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/ptevents/api/filters")
        assert resp.status == 200
        filters = await resp.json()
        assert filters["min_severity"] == "LOW"
        assert filters["enabled_types"] is None


@pytest.mark.asyncio
async def test_put_rejects_invalid_min_severity(prefs_path):
    settings = _base_settings()
    app = _build_app(_FakeDB(), settings, prefs_path=prefs_path)

    async with TestClient(TestServer(app)) as client:
        resp = await client.put("/ptevents/api/filters", json={"min_severity": "BANANA"})
        assert resp.status == 400

    # Bad input must not mutate state nor persist a file.
    assert settings["filters"]["min_severity"] == "LOW"
    assert not prefs_path.exists()


@pytest.mark.asyncio
async def test_put_rejects_non_list_enabled_types(prefs_path):
    settings = _base_settings()
    app = _build_app(_FakeDB(), settings, prefs_path=prefs_path)

    async with TestClient(TestServer(app)) as client:
        # A bare string used to get exploded into chars by list("FIRE").
        resp = await client.put("/ptevents/api/filters", json={"enabled_types": "FIRE"})
        assert resp.status == 400
        # An int used to raise an uncaught TypeError → 500.
        resp = await client.put("/ptevents/api/filters", json={"enabled_types": 123})
        assert resp.status == 400

    assert settings["filters"]["enabled_types"] is None


@pytest.mark.asyncio
async def test_put_normalises_enabled_types_to_uppercase(prefs_path):
    settings = _base_settings()
    app = _build_app(_FakeDB(), settings, prefs_path=prefs_path)

    async with TestClient(TestServer(app)) as client:
        resp = await client.put("/ptevents/api/filters", json={"enabled_types": ["fire", "Storm"]})
        assert resp.status == 200

    assert settings["filters"]["enabled_types"] == ["FIRE", "STORM"]
