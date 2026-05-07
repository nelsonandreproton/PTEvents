"""Tests for Fase 2 collectors: transit providers and AirQualityCollector."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from collectors.transit import (
    HereTransitCollector,
    TomTomCollector,
    TransitCollector,
    WazeCollector,
    _here_event_id,
    _parse_dt,
    _bbox,
)
from collectors.air_quality import AirQualityCollector, _station_event_id, _haversine_km
from models.event import EventType, Severity

HOME_LAT = 38.7169
HOME_LON = -9.1399

_SETTINGS = {
    "location": {"lat": HOME_LAT, "lon": HOME_LON, "radius_km": 10, "name": "Casa"},
    "api_keys": {"here": "test-here-key", "tomtom": "test-tomtom-key"},
    "collectors": {"transit": {"providers": ["waze", "tomtom", "here"]}},
}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def test_parse_dt_valid_z_suffix():
    dt = _parse_dt("2024-01-15T10:30:00Z")
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_dt_valid_no_seconds():
    dt = _parse_dt("2024-01-15T10:30Z")
    assert dt is not None


def test_parse_dt_none_returns_none():
    assert _parse_dt(None) is None


def test_parse_dt_empty_returns_none():
    assert _parse_dt("") is None


def test_bbox_returns_four_bounds():
    bottom, top, left, right = _bbox(38.717, -9.139, 10)
    assert bottom < 38.717 < top
    assert left < -9.139 < right


# ---------------------------------------------------------------------------
# WazeCollector
# ---------------------------------------------------------------------------

def _make_waze_alert(
    alert_type="ACCIDENT",
    subtype="",
    lat=38.720,
    lon=-9.145,
    street="A5",
    reliability=7,
    pub_millis=1705312200000,
) -> dict:
    return {
        "uuid": "waze-001",
        "type": alert_type,
        "subtype": subtype,
        "location": {"y": lat, "x": lon},
        "street": street,
        "reliability": reliability,
        "pubMillis": pub_millis,
        "reportDescription": f"{alert_type} on {street}",
    }


def test_waze_parse_accident():
    collector = WazeCollector()
    alert = _make_waze_alert()
    event = collector._parse_alert(alert)
    assert event is not None
    assert event.type == EventType.ACCIDENT
    assert event.source == "waze"
    assert event.lat == pytest.approx(38.720)
    assert event.lon == pytest.approx(-9.145)


def test_waze_parse_jam_maps_to_congestion():
    collector = WazeCollector()
    alert = _make_waze_alert(alert_type="JAM")
    event = collector._parse_alert(alert)
    assert event is not None
    assert event.type == EventType.CONGESTION


def test_waze_parse_road_closed():
    collector = WazeCollector()
    alert = _make_waze_alert(alert_type="ROAD_CLOSED")
    event = collector._parse_alert(alert)
    assert event is not None
    assert event.type == EventType.ROAD_CLOSURE


def test_waze_parse_subtype_overrides_type():
    collector = WazeCollector()
    alert = _make_waze_alert(alert_type="HAZARD", subtype="HAZARD_ON_ROAD_CONSTRUCTION")
    event = collector._parse_alert(alert)
    assert event is not None
    assert event.type == EventType.ROADWORK


def test_waze_parse_zero_coords_returns_none():
    collector = WazeCollector()
    alert = _make_waze_alert(lat=0, lon=0)
    event = collector._parse_alert(alert)
    assert event is None


def test_waze_parse_title_includes_street():
    collector = WazeCollector()
    alert = _make_waze_alert(street="IC19")
    event = collector._parse_alert(alert)
    assert event is not None
    assert "IC19" in event.title


def test_waze_parse_started_at_from_pub_millis():
    collector = WazeCollector()
    alert = _make_waze_alert(pub_millis=1705312200000)
    event = collector._parse_alert(alert)
    assert event is not None
    assert event.started_at.tzinfo is not None


@pytest.mark.asyncio
async def test_waze_fetch_near_returns_events():
    collector = WazeCollector()
    alert = _make_waze_alert()
    with patch.object(collector, "_fetch", new=AsyncMock(return_value={"alerts": [alert]})):
        events = await collector.fetch_near(HOME_LAT, HOME_LON, 10)
    assert len(events) == 1


@pytest.mark.asyncio
async def test_waze_fetch_near_handles_exception():
    collector = WazeCollector()
    with patch.object(collector, "_fetch", new=AsyncMock(side_effect=Exception("timeout"))):
        with pytest.raises(Exception):
            await collector.fetch_near(HOME_LAT, HOME_LON, 10)


# ---------------------------------------------------------------------------
# TomTomCollector
# ---------------------------------------------------------------------------

def _make_tomtom_incident(
    incident_id="TT-001",
    icon_category=1,
    magnitude=2,
    lat=38.720,
    lon=-9.145,
    road_numbers=None,
    start_time="2024-01-15T09:00:00Z",
    end_time=None,
) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[lon, lat], [lon + 0.001, lat + 0.001]]},
        "properties": {
            "id": incident_id,
            "iconCategory": icon_category,
            "magnitudeOfDelay": magnitude,
            "events": [{"description": "Incident on road"}],
            "startTime": start_time,
            "endTime": end_time,
            "from": "Junction A",
            "roadNumbers": road_numbers or ["A5"],
        },
    }


def test_tomtom_parse_accident():
    collector = TomTomCollector("test-key")
    inc = _make_tomtom_incident(icon_category=1, magnitude=2)
    event = collector._parse_incident(inc)
    assert event is not None
    assert event.type == EventType.ACCIDENT
    assert event.severity == Severity.MEDIUM
    assert event.source == "tomtom"


def test_tomtom_parse_roadwork():
    collector = TomTomCollector("test-key")
    inc = _make_tomtom_incident(icon_category=14)
    event = collector._parse_incident(inc)
    assert event is not None
    assert event.type == EventType.ROADWORK


def test_tomtom_parse_road_closure():
    collector = TomTomCollector("test-key")
    inc = _make_tomtom_incident(icon_category=3, magnitude=4)
    event = collector._parse_incident(inc)
    assert event is not None
    assert event.type == EventType.ROAD_CLOSURE
    assert event.severity == Severity.CRITICAL


def test_tomtom_parse_title_includes_road():
    collector = TomTomCollector("test-key")
    inc = _make_tomtom_incident(road_numbers=["IC19"])
    event = collector._parse_incident(inc)
    assert event is not None
    assert "IC19" in event.title


def test_tomtom_parse_with_end_time():
    collector = TomTomCollector("test-key")
    inc = _make_tomtom_incident(end_time="2024-01-15T12:00:00Z")
    event = collector._parse_incident(inc)
    assert event is not None
    assert event.ends_at is not None


@pytest.mark.asyncio
async def test_tomtom_fetch_near_returns_events():
    collector = TomTomCollector("test-key")
    inc = _make_tomtom_incident()
    with patch.object(collector, "_fetch", new=AsyncMock(return_value={"incidents": [inc]})):
        events = await collector.fetch_near(HOME_LAT, HOME_LON, 10)
    assert len(events) == 1
    assert events[0].source == "tomtom"


@pytest.mark.asyncio
async def test_tomtom_fetch_near_empty():
    collector = TomTomCollector("test-key")
    with patch.object(collector, "_fetch", new=AsyncMock(return_value={"incidents": []})):
        events = await collector.fetch_near(HOME_LAT, HOME_LON, 10)
    assert events == []


# ---------------------------------------------------------------------------
# HereTransitCollector
# ---------------------------------------------------------------------------

def _make_here_incident(
    incident_id="INC-001",
    incident_type="ACCIDENT",
    criticality=2,
    lat=38.720,
    lon=-9.145,
    description="Accident on A5",
    start_time="2024-01-15T09:00:00Z",
    end_time=None,
    road_name="A5",
) -> dict:
    return {
        "id": incident_id,
        "incidentDetails": {
            "type": incident_type,
            "criticality": criticality,
            "description": {"value": description},
            "startTime": start_time,
            "endTime": end_time,
        },
        "location": {
            "shape": {"links": [{"points": [{"lat": lat, "lng": lon}]}]},
            "description": {"value": road_name},
        },
    }


def test_here_event_id_deterministic():
    assert _here_event_id("abc") == _here_event_id("abc")


def test_here_event_id_different_inputs_differ():
    assert _here_event_id("abc") != _here_event_id("def")


def test_here_parse_accident():
    collector = HereTransitCollector("test-key")
    item = _make_here_incident()
    event = collector._parse_incident(item)
    assert event is not None
    assert event.type == EventType.ACCIDENT
    assert event.severity == Severity.MEDIUM
    assert event.source == "here_transit"


def test_here_parse_road_closure():
    collector = HereTransitCollector("test-key")
    item = _make_here_incident(incident_type="ROAD_CLOSURE", criticality=3)
    event = collector._parse_incident(item)
    assert event is not None
    assert event.type == EventType.ROAD_CLOSURE
    assert event.severity == Severity.HIGH


def test_here_parse_zero_coords_returns_none():
    collector = HereTransitCollector("test-key")
    item = _make_here_incident(lat=0, lon=0)
    event = collector._parse_incident(item)
    assert event is None


def test_here_parse_title_includes_road():
    collector = HereTransitCollector("test-key")
    item = _make_here_incident(road_name="IC19")
    event = collector._parse_incident(item)
    assert event is not None
    assert "IC19" in event.title


@pytest.mark.asyncio
async def test_here_fetch_near_no_key_returns_empty():
    collector = HereTransitCollector("")
    events = await collector.fetch_near(HOME_LAT, HOME_LON, 10)
    assert events == []


@pytest.mark.asyncio
async def test_here_fetch_near_returns_events():
    collector = HereTransitCollector("test-key")
    with patch.object(
        collector, "_fetch_incidents", new=AsyncMock(return_value={"results": [_make_here_incident()]})
    ):
        events = await collector.fetch_near(HOME_LAT, HOME_LON, 10)
    assert len(events) == 1


# ---------------------------------------------------------------------------
# TransitCollector facade
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_facade_uses_waze_first():
    collector = TransitCollector(_SETTINGS)
    waze_provider = collector._providers[0][1]
    with patch.object(waze_provider, "fetch_near", new=AsyncMock(return_value=[])) as mock_waze:
        await collector.fetch()
    mock_waze.assert_called_once()


@pytest.mark.asyncio
async def test_facade_falls_back_to_tomtom_when_waze_fails():
    collector = TransitCollector(_SETTINGS)
    waze_provider = collector._providers[0][1]
    tomtom_provider = collector._providers[1][1]

    with patch.object(waze_provider, "fetch_near", new=AsyncMock(side_effect=Exception("waze down"))):
        with patch.object(tomtom_provider, "fetch_near", new=AsyncMock(return_value=[])) as mock_tomtom:
            await collector.fetch()
    mock_tomtom.assert_called_once()


@pytest.mark.asyncio
async def test_facade_returns_waze_events_without_calling_others():
    from models.event import Event, Severity
    import hashlib
    fake_event = Event(
        id="fake001",
        source="waze",
        type=EventType.ACCIDENT,
        title="Test",
        description="desc",
        lat=HOME_LAT,
        lon=HOME_LON,
        severity=Severity.LOW,
        status="active",
        started_at=datetime.now(timezone.utc),
    )

    collector = TransitCollector(_SETTINGS)
    waze_provider = collector._providers[0][1]
    tomtom_provider = collector._providers[1][1]

    with patch.object(waze_provider, "fetch_near", new=AsyncMock(return_value=[fake_event])):
        with patch.object(tomtom_provider, "fetch_near", new=AsyncMock()) as mock_tomtom:
            events = await collector.fetch()

    assert len(events) == 1
    mock_tomtom.assert_not_called()


@pytest.mark.asyncio
async def test_facade_all_providers_fail_returns_empty():
    collector = TransitCollector(_SETTINGS)
    for _, provider in collector._providers:
        patch.object(provider, "fetch_near", new=AsyncMock(side_effect=Exception("down"))).start()
    events = await collector.fetch()
    assert events == []


@pytest.mark.asyncio
async def test_facade_no_keys_only_waze():
    settings = {
        "location": _SETTINGS["location"],
        "api_keys": {"here": "", "tomtom": ""},
        "collectors": {"transit": {"providers": ["waze", "tomtom", "here"]}},
    }
    collector = TransitCollector(settings)
    provider_names = [name for name, _ in collector._providers]
    assert provider_names == ["waze"]


# ---------------------------------------------------------------------------
# AirQualityCollector helpers
# ---------------------------------------------------------------------------

def test_station_event_id_deterministic():
    assert _station_event_id(1021, "2024-01-15") == _station_event_id(1021, "2024-01-15")


def test_station_event_id_different_station_differs():
    assert _station_event_id(1021, "2024-01-15") != _station_event_id(1022, "2024-01-15")


def test_haversine_km_same_point():
    assert _haversine_km(38.717, -9.139, 38.717, -9.139) == pytest.approx(0.0)


def test_haversine_km_known_distance():
    dist = _haversine_km(38.7169, -9.1399, 41.1496, -8.6109)
    assert abs(dist - 274) <= 5


# ---------------------------------------------------------------------------
# AirQualityCollector.fetch
# ---------------------------------------------------------------------------

def _make_apa_station(
    station_id: int = 1021,
    lat: float = 38.720,
    lon: float = -9.145,
    iqar_index: int = 4,
    station_name: str = "Lisboa - Entrecampos",
    dominant_pol: str = "NO2",
    vals: str = "85 µg/m³",
) -> dict:
    return {
        "estacao_id": station_id,
        "estacao_nome": station_name,
        "latitude": lat,
        "longitude": lon,
        "indice": iqar_index,
        "pols": dominant_pol,
        "vals": vals,
    }


@pytest.mark.asyncio
async def test_apa_fetch_returns_alert_for_bad_index():
    station = _make_apa_station(iqar_index=4)
    collector = AirQualityCollector(_SETTINGS)
    with patch.object(collector, "_fetch_map", new=AsyncMock(return_value={"stations": [station]})):
        events = await collector.fetch()
    assert len(events) == 1
    assert events[0].type == EventType.AIR_QUALITY
    assert events[0].severity == Severity.HIGH


@pytest.mark.asyncio
async def test_apa_fetch_no_alert_for_good_index():
    station = _make_apa_station(iqar_index=2)
    collector = AirQualityCollector(_SETTINGS)
    with patch.object(collector, "_fetch_map", new=AsyncMock(return_value={"stations": [station]})):
        events = await collector.fetch()
    assert events == []


@pytest.mark.asyncio
async def test_apa_fetch_critical_index_5():
    station = _make_apa_station(iqar_index=5)
    collector = AirQualityCollector(_SETTINGS)
    with patch.object(collector, "_fetch_map", new=AsyncMock(return_value={"stations": [station]})):
        events = await collector.fetch()
    assert len(events) == 1
    assert events[0].severity == Severity.CRITICAL


@pytest.mark.asyncio
async def test_apa_fetch_skips_station_too_far():
    station = _make_apa_station(lat=41.1496, lon=-8.6109, iqar_index=4)
    collector = AirQualityCollector(_SETTINGS)
    with patch.object(collector, "_fetch_map", new=AsyncMock(return_value={"stations": [station]})):
        events = await collector.fetch()
    assert events == []


@pytest.mark.asyncio
async def test_apa_fetch_skips_zero_coord_station():
    station = _make_apa_station(lat=0, lon=0, iqar_index=5)
    collector = AirQualityCollector(_SETTINGS)
    with patch.object(collector, "_fetch_map", new=AsyncMock(return_value={"stations": [station]})):
        events = await collector.fetch()
    assert events == []


@pytest.mark.asyncio
async def test_apa_fetch_handles_exception():
    collector = AirQualityCollector(_SETTINGS)
    with patch.object(collector, "_fetch_map", new=AsyncMock(side_effect=Exception("timeout"))):
        events = await collector.fetch()
    assert events == []


@pytest.mark.asyncio
async def test_apa_fetch_description_contains_pollutant():
    station = _make_apa_station(iqar_index=4, dominant_pol="PM10")
    collector = AirQualityCollector(_SETTINGS)
    with patch.object(collector, "_fetch_map", new=AsyncMock(return_value={"stations": [station]})):
        events = await collector.fetch()
    assert len(events) == 1
    assert "PM10" in events[0].description
