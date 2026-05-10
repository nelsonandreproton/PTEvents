"""Tests for IpmaCollector: district matching, type mapping, severity mapping."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from collectors.ipma import (
    IpmaCollector,
    _nearest_districts,
    DISTRICT_CENTROIDS,
    AWARENESS_TYPE_TO_EVENT_TYPE,
    AWARENESS_LEVEL_TO_SEVERITY,
)
from models.event import EventType, Severity

HOME_LAT = 38.8097   # Odivelas
HOME_LON = -9.2518

_SETTINGS = {
    "location": {"lat": HOME_LAT, "lon": HOME_LON, "radius_km": 5, "name": "Casa"},
    "collectors": {"ipma": {"enabled": True}},
}

# ---------------------------------------------------------------------------
# District detection

def test_nearest_districts_odivelas_returns_lsb_and_mcs():
    districts = _nearest_districts(HOME_LAT, HOME_LON, max_districts=2)
    assert "LSB" in districts or "MCS" in districts, (
        f"Expected LSB or MCS near Odivelas, got {districts}"
    )

def test_nearest_districts_returns_requested_count():
    assert len(_nearest_districts(HOME_LAT, HOME_LON, max_districts=3)) == 3

def test_nearest_districts_porto():
    # Porto at ~41.15, -8.61 — nearest should be PTO
    districts = _nearest_districts(41.1496, -8.6109, max_districts=1)
    assert districts == ["PTO"]

# ---------------------------------------------------------------------------
# Type mapping covers Portuguese names

@pytest.mark.parametrize("pt_name,expected", [
    ("Precipitação", EventType.RAIN),
    ("Trovoada", EventType.STORM),
    ("Vento", EventType.WIND),
    ("Neve", EventType.COLD),
    ("Tempo Frio", EventType.COLD),
    ("Tempo Quente", EventType.HEAT),
    ("Nevoeiro", EventType.RAIN),
    ("Agitação Marítima", EventType.FLOOD),
])
def test_portuguese_type_names_map_correctly(pt_name, expected):
    assert AWARENESS_TYPE_TO_EVENT_TYPE[pt_name] == expected

# ---------------------------------------------------------------------------
# Severity mapping

@pytest.mark.parametrize("level,expected", [
    ("yellow", Severity.MEDIUM),
    ("orange", Severity.HIGH),
    ("red", Severity.CRITICAL),
])
def test_severity_mapping(level, expected):
    assert AWARENESS_LEVEL_TO_SEVERITY[level] == expected

# ---------------------------------------------------------------------------
# _parse_warning: green warnings are skipped

def test_parse_warning_skips_green():
    collector = IpmaCollector(_SETTINGS)
    item = {
        "idAreaAviso": "LSB",
        "awarenessTypeName": "Precipitação",
        "awarenessLevelID": "green",
        "startTime": "2026-05-10T12:00:00",
        "endTime": "2026-05-11T00:00:00",
        "text": "Normal.",
    }
    assert collector._parse_warning(item) is None

def test_parse_warning_yellow_rain():
    collector = IpmaCollector(_SETTINGS)
    item = {
        "idAreaAviso": "LSB",
        "awarenessTypeName": "Precipitação",
        "awarenessLevelID": "yellow",
        "startTime": "2026-05-10T12:00:00",
        "endTime": "2026-05-11T00:00:00",
        "text": "Precipitação forte.",
    }
    event = collector._parse_warning(item)
    assert event is not None
    assert event.type == EventType.RAIN
    assert event.severity == Severity.MEDIUM
    assert event.source == "ipma"

# ---------------------------------------------------------------------------
# collect(): district matching, not radius

@pytest.mark.asyncio
async def test_collect_accepts_lsb_warning_for_odivelas():
    """LSB warning should reach Odivelas even though centroid is 14 km away."""
    collector = IpmaCollector(_SETTINGS)

    lsb_warning = {
        "idAreaAviso": "LSB",
        "awarenessTypeName": "Precipitação",
        "awarenessLevelID": "yellow",
        "startTime": "2026-05-10T12:00:00",
        "endTime": "2026-05-11T00:00:00",
        "text": "Chuva forte.",
    }
    other_warning = {
        "idAreaAviso": "BRG",  # Braga — far away
        "awarenessTypeName": "Precipitação",
        "awarenessLevelID": "yellow",
        "startTime": "2026-05-10T12:00:00",
        "endTime": "2026-05-11T00:00:00",
        "text": "Chuva.",
    }

    with patch.object(collector, "_fetch_warnings", new=AsyncMock(return_value=[lsb_warning, other_warning])):
        with patch.object(collector, "_fetch_seismic", new=AsyncMock(return_value=[])):
            events = await collector.collect(HOME_LAT, HOME_LON, radius_km=5)

    types_areas = [(e.type, e.raw.get("idAreaAviso")) for e in events]
    assert any(area == "LSB" for _, area in types_areas), "LSB warning should be included"
    assert all(area != "BRG" for _, area in types_areas), "BRG warning should be excluded"

@pytest.mark.asyncio
async def test_collect_excludes_distant_district():
    collector = IpmaCollector(_SETTINGS)

    far_warning = {
        "idAreaAviso": "FAR",  # Faro — south, not near Odivelas
        "awarenessTypeName": "Vento",
        "awarenessLevelID": "orange",
        "startTime": "2026-05-10T12:00:00",
        "endTime": "2026-05-11T00:00:00",
        "text": "Vento forte.",
    }

    with patch.object(collector, "_fetch_warnings", new=AsyncMock(return_value=[far_warning])):
        with patch.object(collector, "_fetch_seismic", new=AsyncMock(return_value=[])):
            events = await collector.collect(HOME_LAT, HOME_LON, radius_km=5)

    assert events == [], "Faro warning should not reach Odivelas"

@pytest.mark.asyncio
async def test_collect_seismic_uses_radius():
    """Seismic events (no idAreaAviso) still use haversine radius."""
    collector = IpmaCollector(_SETTINGS)

    nearby_seismic = {
        "lat": HOME_LAT + 0.01,
        "lon": HOME_LON + 0.01,
        "mag": 3.0,
        "time": "2026-05-10T12:00:00",
        "depth": 10,
    }
    far_seismic = {
        "lat": 41.0,
        "lon": -8.5,
        "mag": 3.0,
        "time": "2026-05-10T12:00:00",
        "depth": 10,
    }

    with patch.object(collector, "_fetch_warnings", new=AsyncMock(return_value=[])):
        with patch.object(collector, "_fetch_seismic", new=AsyncMock(return_value=[nearby_seismic, far_seismic])):
            events = await collector.collect(HOME_LAT, HOME_LON, radius_km=5)

    assert len(events) == 1
    assert abs(events[0].lat - (HOME_LAT + 0.01)) < 0.001
