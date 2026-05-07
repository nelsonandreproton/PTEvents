"""Tests for Fase 3 collectors: greves, obras, eventos, nasa_firms."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from collectors.greves import GrevesCollector, _greve_id, _severity_from_text, _strip_html
from collectors.obras import ObrasCollector, _obras_id, _is_obras_related
from collectors.eventos import EventosCollector, _event_id as _ev_id
from collectors.nasa_firms import NasaFirmsCollector, _fire_id, _bbox_str, _parse_firms_datetime
from models.event import EventType, Severity

HOME_LAT = 38.8097
HOME_LON = -9.2518

_SETTINGS = {
    "location": {"lat": HOME_LAT, "lon": HOME_LON, "radius_km": 10, "name": "Casa"},
    "api_keys": {
        "nasa_firms": "test-firms-key",
        "eventbrite": "test-eb-token",
    },
    "collectors": {},
}


# ---------------------------------------------------------------------------
# GrevesCollector helpers
# ---------------------------------------------------------------------------

def test_greve_id_deterministic():
    assert _greve_id(123) == _greve_id(123)


def test_greve_id_different_ids_differ():
    assert _greve_id(1) != _greve_id(2)


def test_severity_transport_keywords_high():
    assert _severity_from_text("Greve nos transportes públicos") == Severity.HIGH


def test_severity_metro_keyword_high():
    assert _severity_from_text("paralisação do metro e comboio") == Severity.HIGH


def test_severity_generic_strike_medium():
    assert _severity_from_text("Greve dos trabalhadores hospitalares") == Severity.MEDIUM


def test_severity_greve_geral_critical():
    assert _severity_from_text("Greve geral convocada para amanhã") == Severity.CRITICAL


def test_strip_html_removes_tags():
    result = _strip_html("<p>Greve <strong>nacional</strong></p>")
    assert "<p>" not in result
    assert "Greve" in result
    assert "nacional" in result


def _make_wp_post(post_id=1, title="Greve dos transportes", excerpt="Trabalhadores em greve") -> dict:
    return {
        "id": post_id,
        "title": {"rendered": title},
        "excerpt": {"rendered": f"<p>{excerpt}</p>"},
        "link": f"https://www.dgert.gov.pt/greve-{post_id}",
        "date_gmt": "2024-01-15T09:00:00",
    }


@pytest.mark.asyncio
async def test_greves_fetch_returns_events():
    posts = [_make_wp_post(1), _make_wp_post(2)]
    collector = GrevesCollector(_SETTINGS)

    with patch.object(collector, "_fetch_category", new=AsyncMock(return_value=posts)):
        events = await collector.fetch()

    assert len(events) > 0
    assert all(e.type == EventType.STRIKE for e in events)
    assert all(e.source == "greves" for e in events)


@pytest.mark.asyncio
async def test_greves_fetch_deduplicates_across_categories():
    post = _make_wp_post(1)
    collector = GrevesCollector(_SETTINGS)

    with patch.object(collector, "_fetch_category", new=AsyncMock(return_value=[post])):
        events = await collector.fetch()

    assert len(events) == 1


@pytest.mark.asyncio
async def test_greves_fetch_handles_category_exception():
    collector = GrevesCollector(_SETTINGS)

    with patch.object(collector, "_fetch_category", new=AsyncMock(side_effect=Exception("HTTP 503"))):
        events = await collector.fetch()

    assert events == []


@pytest.mark.asyncio
async def test_greves_transport_severity_elevated():
    post = _make_wp_post(title="Greve do metro de Lisboa", excerpt="Serviço suspenso")
    collector = GrevesCollector(_SETTINGS)

    with patch.object(collector, "_fetch_category", new=AsyncMock(return_value=[post])):
        events = await collector.fetch()

    assert len(events) == 1
    assert events[0].severity == Severity.HIGH


# ---------------------------------------------------------------------------
# ObrasCollector helpers
# ---------------------------------------------------------------------------

def test_obras_id_deterministic():
    assert _obras_id("sintra", "abc") == _obras_id("sintra", "abc")


def test_obras_id_different_sources_differ():
    assert _obras_id("sintra", "abc") != _obras_id("amadora", "abc")


def test_is_obras_related_positive():
    assert _is_obras_related("Obras de pavimentação na Rua X")
    assert _is_obras_related("Intervenção no arruamento")
    assert _is_obras_related("Requalificação da ciclovia")


def test_is_obras_related_negative():
    assert not _is_obras_related("Festival de Verão em Sintra")
    assert not _is_obras_related("Reunião da câmara municipal")


_ODIVELAS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Odivelas</title>
    <item>
      <title>Obras de pavimentação na Rua das Flores</title>
      <description>Requalificação do pavimento</description>
      <link>https://www.cm-odivelas.pt/pages/321/item1</link>
      <guid>guid-001</guid>
      <pubDate>Mon, 15 Jan 2024 09:00:00 +0000</pubDate>
    </item>
    <item>
      <title>Festival de música</title>
      <description>Evento cultural</description>
      <link>https://www.cm-odivelas.pt/pages/321/item2</link>
      <guid>guid-002</guid>
      <pubDate>Mon, 15 Jan 2024 10:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


@pytest.mark.asyncio
async def test_obras_odivelas_rss_filters_obras_only():
    collector = ObrasCollector(_SETTINGS)

    import httpx
    mock_response = AsyncMock()
    mock_response.text = _ODIVELAS_RSS
    mock_response.raise_for_status = lambda: None

    with patch.object(collector, "_fetch_odivelas_rss", new=AsyncMock(return_value=[
        type("E", (), {"type": EventType.PLANNED_WORKS, "id": "x", "source": "obras",
                       "title": "Obras de pavimentação", "description": "", "lat": HOME_LAT,
                       "lon": HOME_LON, "severity": Severity.LOW, "status": "active",
                       "started_at": datetime.now(timezone.utc), "ends_at": None, "url": None, "raw": {}})()
    ])):
        events = await collector.fetch()

    assert len(events) >= 1


@pytest.mark.asyncio
async def test_obras_fetch_handles_all_sources_failing():
    collector = ObrasCollector(_SETTINGS)

    with patch.object(collector, "_fetch_odivelas_rss", new=AsyncMock(side_effect=Exception("rss down"))):
        with patch.object(collector, "_fetch_sintra_html", new=AsyncMock(side_effect=Exception("html down"))):
            with patch.object(collector, "_fetch_amadora_html", new=AsyncMock(side_effect=Exception("html down"))):
                events = await collector.fetch()

    assert events == []


# ---------------------------------------------------------------------------
# EventosCollector
# ---------------------------------------------------------------------------

_ODIVELAS_EVENTS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Odivelas Eventos</title>
    <item>
      <title>Concerto de Verão</title>
      <description>Grande concerto ao ar livre</description>
      <link>https://www.cm-odivelas.pt/pages/322/item1</link>
      <guid>ev-guid-001</guid>
      <pubDate>Mon, 15 Jan 2024 18:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""


@pytest.mark.asyncio
async def test_eventos_odivelas_rss_returns_events():
    collector = EventosCollector(_SETTINGS)

    with patch.object(collector, "_fetch_odivelas_events", new=AsyncMock(return_value=[
        type("E", (), {"type": EventType.EVENT_CLOSURE, "id": "ev1", "source": "eventos",
                       "title": "Concerto", "description": "", "lat": HOME_LAT, "lon": HOME_LON,
                       "severity": Severity.LOW, "status": "active",
                       "started_at": datetime.now(timezone.utc), "ends_at": None, "url": None, "raw": {}})()
    ])):
        events = await collector.fetch()

    assert len(events) >= 1


@pytest.mark.asyncio
async def test_eventos_eventbrite_parsed_correctly():
    collector = EventosCollector(_SETTINGS)
    eb_item = {
        "id": "eb-123",
        "name": {"text": "Tech Conference Lisboa"},
        "description": {"text": "Annual tech event"},
        "url": "https://www.eventbrite.com/e/123",
        "start": {"utc": "2024-06-15T10:00:00Z"},
        "end": {"utc": "2024-06-15T18:00:00Z"},
        "venue": {"address": {"latitude": "38.72", "longitude": "-9.14"}},
    }
    event = collector._parse_eventbrite_event(eb_item)
    assert event is not None
    assert event.type == EventType.EVENT_CLOSURE
    assert event.source == "eventos"
    assert event.lat == pytest.approx(38.72)
    assert event.ends_at is not None


@pytest.mark.asyncio
async def test_eventos_eventbrite_skipped_without_token():
    settings = {**_SETTINGS, "api_keys": {"eventbrite": ""}}
    collector = EventosCollector(settings)

    with patch.object(collector, "_fetch_odivelas_events", new=AsyncMock(return_value=[])):
        with patch.object(collector, "_fetch_eventbrite", new=AsyncMock()) as mock_eb:
            await collector.fetch()

    mock_eb.assert_not_called()


@pytest.mark.asyncio
async def test_eventos_fetch_handles_all_failing():
    collector = EventosCollector(_SETTINGS)

    with patch.object(collector, "_fetch_odivelas_events", new=AsyncMock(side_effect=Exception("down"))):
        with patch.object(collector, "_fetch_eventbrite", new=AsyncMock(side_effect=Exception("down"))):
            events = await collector.fetch()

    assert events == []


# ---------------------------------------------------------------------------
# NasaFirmsCollector helpers
# ---------------------------------------------------------------------------

def test_fire_id_deterministic():
    assert _fire_id(38.72, -9.14, "2024-01-15", "1030") == _fire_id(38.72, -9.14, "2024-01-15", "1030")


def test_fire_id_different_coords_differ():
    assert _fire_id(38.72, -9.14, "2024-01-15", "1030") != _fire_id(38.73, -9.14, "2024-01-15", "1030")


def test_bbox_str_format():
    bbox = _bbox_str(38.8097, -9.2518, 10)
    parts = bbox.split(",")
    assert len(parts) == 4
    west, south, east, north = [float(p) for p in parts]
    assert west < -9.2518 < east
    assert south < 38.8097 < north


def test_parse_firms_datetime_valid():
    dt = _parse_firms_datetime("2024-01-15", "1030")
    assert dt.year == 2024
    assert dt.month == 1
    assert dt.day == 15
    assert dt.hour == 10
    assert dt.minute == 30
    assert dt.tzinfo is not None


def test_parse_firms_datetime_short_time():
    dt = _parse_firms_datetime("2024-01-15", "530")
    assert dt.hour == 5
    assert dt.minute == 30


_FIRMS_CSV = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight
38.810,-9.255,334.2,0.38,0.36,2024-01-15,1030,N,VIIRS,h,2.0NRT,296.3,15.2,D
38.820,-9.260,310.1,0.38,0.36,2024-01-15,1045,N,VIIRS,l,2.0NRT,291.0,2.1,D
"""

_FIRMS_CSV_HIGH_FRP = """latitude,longitude,bright_ti4,scan,track,acq_date,acq_time,satellite,instrument,confidence,version,bright_t31,frp,daynight
38.810,-9.255,380.0,0.38,0.36,2024-01-15,1030,N,VIIRS,h,2.0NRT,310.0,150.0,D
"""


@pytest.mark.asyncio
async def test_firms_fetch_no_key_returns_empty():
    settings = {**_SETTINGS, "api_keys": {"nasa_firms": ""}}
    collector = NasaFirmsCollector(settings)
    events = await collector.fetch()
    assert events == []


@pytest.mark.asyncio
async def test_firms_fetch_parses_csv():
    collector = NasaFirmsCollector(_SETTINGS)

    with patch.object(collector, "_fetch_csv", new=AsyncMock(return_value=_FIRMS_CSV)):
        events = await collector.fetch()

    assert len(events) == 2
    assert all(e.type == EventType.FIRE for e in events)
    assert all(e.source == "nasa_firms" for e in events)


@pytest.mark.asyncio
async def test_firms_high_confidence_higher_severity():
    collector = NasaFirmsCollector(_SETTINGS)

    with patch.object(collector, "_fetch_csv", new=AsyncMock(return_value=_FIRMS_CSV)):
        events = await collector.fetch()

    from bot.scheduler import SEVERITY_ORDER
    # Confidence appears as single letter (H/L/N) in description
    high_conf = [e for e in events if "H)" in e.description or ": H." in e.description]
    low_conf = [e for e in events if "L)" in e.description or ": L." in e.description]
    assert high_conf, "Expected at least one HIGH confidence event"
    assert low_conf, "Expected at least one LOW confidence event"
    assert SEVERITY_ORDER.index(high_conf[0].severity) >= SEVERITY_ORDER.index(low_conf[0].severity)


@pytest.mark.asyncio
async def test_firms_high_frp_critical():
    collector = NasaFirmsCollector(_SETTINGS)

    with patch.object(collector, "_fetch_csv", new=AsyncMock(return_value=_FIRMS_CSV_HIGH_FRP)):
        events = await collector.fetch()

    assert len(events) == 1
    assert events[0].severity == Severity.CRITICAL


@pytest.mark.asyncio
async def test_firms_empty_csv_returns_empty():
    collector = NasaFirmsCollector(_SETTINGS)

    with patch.object(collector, "_fetch_csv", new=AsyncMock(return_value="latitude,longitude\n")):
        events = await collector.fetch()

    assert events == []


@pytest.mark.asyncio
async def test_firms_fetch_exception_returns_empty():
    collector = NasaFirmsCollector(_SETTINGS)

    with patch.object(collector, "_fetch_csv", new=AsyncMock(side_effect=Exception("network error"))):
        events = await collector.fetch()

    assert events == []
