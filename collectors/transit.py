import hashlib
import logging
import math
from datetime import datetime, timezone

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from models.event import Event, EventType, Severity

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _no_retry_on_429(exc: BaseException) -> bool:
    return "429" not in str(exc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    value = value.strip().replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M%z"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _bbox(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    """Return (bottom, top, left, right) bounding box for a circle."""
    delta_lat = radius_km / 111.0
    delta_lon = radius_km / (111.0 * math.cos(math.radians(lat)))
    return lat - delta_lat, lat + delta_lat, lon - delta_lon, lon + delta_lon


# ---------------------------------------------------------------------------
# Waze
# ---------------------------------------------------------------------------

WAZE_URL = "https://www.waze.com/live-map/api/georss"

_WAZE_TYPE_MAP: dict[str, EventType] = {
    "ACCIDENT": EventType.ACCIDENT,
    "JAM": EventType.CONGESTION,
    "ROAD_CLOSED": EventType.ROAD_CLOSURE,
    "HAZARD": EventType.ACCIDENT,
    "CONSTRUCTION": EventType.ROADWORK,
}

_WAZE_SUBTYPE_MAP: dict[str, EventType] = {
    "HAZARD_ON_ROAD_CONSTRUCTION": EventType.ROADWORK,
    "HAZARD_ON_ROAD_CAR_STOPPED": EventType.ACCIDENT,
    "HAZARD_ON_ROAD_OBJECT": EventType.ACCIDENT,
    "ROAD_CLOSED_CONSTRUCTION": EventType.ROADWORK,
    "ROAD_CLOSED_EVENT": EventType.EVENT_CLOSURE,
    "ROAD_CLOSED_HAZARD": EventType.ROAD_CLOSURE,
}

# Severity by event type — reliability is a crowdsource confidence count, not impact
_WAZE_TYPE_SEVERITY: dict[str, Severity] = {
    "ACCIDENT": Severity.HIGH,
    "JAM": Severity.MEDIUM,
    "ROAD_CLOSED": Severity.HIGH,
    "HAZARD": Severity.MEDIUM,
    "CONSTRUCTION": Severity.LOW,
}

_WAZE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.waze.com/live-map",
}


def _waze_event_id(alert: dict) -> str:
    uid = alert.get("uuid") or alert.get("id") or str(alert.get("location", {}))
    return hashlib.sha256(f"waze_{uid}".encode()).hexdigest()[:16]


class WazeCollector:
    source_name = "waze"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch(
        self, client: httpx.AsyncClient, bottom: float, top: float, left: float, right: float
    ) -> dict:
        params = {
            "bottom": bottom,
            "top": top,
            "left": left,
            "right": right,
            "types": "alerts,traffic",
            "ma": 600,
        }
        response = await client.get(WAZE_URL, params=params, headers=_WAZE_HEADERS)
        response.raise_for_status()
        return response.json()

    def _parse_alert(self, alert: dict) -> Event | None:
        try:
            alert_type = (alert.get("type") or "").upper()
            subtype = (alert.get("subtype") or "").upper()

            event_type = _WAZE_SUBTYPE_MAP.get(subtype) or _WAZE_TYPE_MAP.get(alert_type, EventType.ACCIDENT)

            severity = _WAZE_TYPE_SEVERITY.get(alert_type, Severity.LOW)

            loc = alert.get("location", {})
            lat = float(loc.get("y") or loc.get("lat") or 0)
            lon = float(loc.get("x") or loc.get("lon") or 0)

            if lat == 0 and lon == 0:
                return None

            street = alert.get("street") or alert.get("roadType") or ""
            description = alert.get("reportDescription") or alert.get("text") or street or alert_type.replace("_", " ").title()
            title = f"{alert_type.replace('_', ' ').title()}"
            if street:
                title += f" — {street}"

            pub_ms = alert.get("pubMillis")
            started_at = (
                datetime.fromtimestamp(pub_ms / 1000, tz=timezone.utc)
                if pub_ms
                else datetime.now(timezone.utc)
            )

            return Event(
                id=_waze_event_id(alert),
                source=self.source_name,
                type=event_type,
                title=title,
                description=description,
                lat=lat,
                lon=lon,
                severity=severity,
                status="active",
                started_at=started_at,
                ends_at=None,
                url=None,
                raw=alert,
            )
        except Exception:
            logger.exception("WazeCollector: failed to parse alert")
            return None

    async def fetch_near(self, lat: float, lon: float, radius_km: float) -> list[Event]:
        bottom, top, left, right = _bbox(lat, lon, radius_km)
        async with httpx.AsyncClient(timeout=15.0) as client:
            data = await self._fetch(client, bottom, top, left, right)

        alerts = data.get("alerts") or []
        events: list[Event] = []
        for alert in alerts:
            event = self._parse_alert(alert)
            if event is not None:
                events.append(event)

        logger.info("WazeCollector: %d alerts → %d events", len(alerts), len(events))
        return events


# ---------------------------------------------------------------------------
# TomTom
# ---------------------------------------------------------------------------

TOMTOM_URL = "https://api.tomtom.com/traffic/services/5/incidentDetails"

_TOMTOM_ICON_MAP: dict[int, EventType] = {
    1: EventType.ACCIDENT,
    2: EventType.CONGESTION,
    3: EventType.ROAD_CLOSURE,
    4: EventType.ROADWORK,
    5: EventType.ROAD_CLOSURE,
    6: EventType.ROAD_CLOSURE,
    7: EventType.CONGESTION,
    8: EventType.ACCIDENT,
    9: EventType.ROAD_CLOSURE,
    10: EventType.CONGESTION,
    11: EventType.ACCIDENT,
    14: EventType.ROADWORK,
}

_TOMTOM_MAG_MAP: dict[int, Severity] = {
    1: Severity.LOW,
    2: Severity.MEDIUM,
    3: Severity.HIGH,
    4: Severity.CRITICAL,
}


def _tomtom_event_id(incident_id: str) -> str:
    return hashlib.sha256(f"tomtom_{incident_id}".encode()).hexdigest()[:16]


class TomTomCollector:
    source_name = "tomtom"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch(
        self, client: httpx.AsyncClient, bbox: str
    ) -> dict:
        params = {
            "key": self._api_key,
            "bbox": bbox,
            "fields": "{incidents{type,geometry{type,coordinates},properties{id,iconCategory,magnitudeOfDelay,events{description,code,iconCategory},startTime,endTime,from,to,length,delay,roadNumbers,timeValidity}}}",
            "language": "pt-PT",
            "t": -1,
            "categoryFilter": "0,1,2,3,4,5,6,7,8,9,10,11,14",
            "timeValidityFilter": "present",
        }
        response = await client.get(TOMTOM_URL, params=params)
        response.raise_for_status()
        return response.json()

    def _parse_incident(self, incident: dict) -> Event | None:
        try:
            props = incident.get("properties", {})
            incident_id = props.get("id", "")

            icon_cat = int(props.get("iconCategory") or 0)
            event_type = _TOMTOM_ICON_MAP.get(icon_cat, EventType.ACCIDENT)

            mag = int(props.get("magnitudeOfDelay") or 1)
            severity = _TOMTOM_MAG_MAP.get(mag, Severity.LOW)

            events_list = props.get("events") or []

            # Skip permanently closed incidents (code 401 = "Encerrado/a")
            event_codes = {int(e.get("code", 0)) for e in events_list}
            if 401 in event_codes:
                return None

            description_parts = [e.get("description", "") for e in events_list if e.get("description")]
            from_str = props.get("from", "")
            to_str = props.get("to", "")
            delay_s = props.get("delay")
            length_m = props.get("length")

            if delay_s:
                description_parts.append(f"Demora: {int(delay_s // 60)} min")
            if length_m:
                description_parts.append(f"Extensão: {length_m / 1000:.1f} km")
            description = "; ".join(description_parts) or from_str

            road = props.get("roadNumbers") or []
            road_str = ", ".join(road) if isinstance(road, list) else str(road)

            title = f"{event_type.value.replace('_', ' ').title()}"
            if road_str:
                title += f" — {road_str}"
                if from_str:
                    title += f" ({from_str})"
            elif from_str and to_str and from_str != to_str:
                title += f" — {from_str} → {to_str}"
            elif from_str:
                title += f" — {from_str}"

            geo = incident.get("geometry", {})
            coords = geo.get("coordinates", [])
            if geo.get("type") == "LineString" and coords:
                pt = coords[0]
            elif geo.get("type") == "Point" and coords:
                pt = coords
            else:
                return None
            lon = float(pt[0])
            lat = float(pt[1])

            if lat == 0 and lon == 0:
                return None

            started_at = _parse_dt(props.get("startTime")) or datetime.now(timezone.utc)
            ends_at = _parse_dt(props.get("endTime"))

            return Event(
                id=_tomtom_event_id(incident_id),
                source=self.source_name,
                type=event_type,
                title=title,
                description=description,
                lat=lat,
                lon=lon,
                severity=severity,
                status="active",
                started_at=started_at,
                ends_at=ends_at,
                url=None,
                raw=incident,
            )
        except Exception:
            logger.exception("TomTomCollector: failed to parse incident")
            return None

    async def fetch_near(self, lat: float, lon: float, radius_km: float) -> list[Event]:
        bottom, top, left, right = _bbox(lat, lon, radius_km)
        bbox = f"{left},{bottom},{right},{top}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            data = await self._fetch(client, bbox)

        incidents = data.get("incidents") or []
        events: list[Event] = []
        for inc in incidents:
            event = self._parse_incident(inc)
            if event is not None:
                events.append(event)

        logger.info("TomTomCollector: %d incidents → %d events", len(incidents), len(events))
        return events


# ---------------------------------------------------------------------------
# HERE
# ---------------------------------------------------------------------------

HERE_INCIDENTS_URL = "https://data.traffic.hereapi.com/v7/incidents"

_HERE_TYPE_MAP: dict[str, EventType] = {
    "ACCIDENT": EventType.ACCIDENT,
    "CONGESTION": EventType.CONGESTION,
    "ROAD_CLOSURE": EventType.ROAD_CLOSURE,
    "CONSTRUCTION": EventType.ROADWORK,
    "ROAD_HAZARD": EventType.ACCIDENT,
    "LANE_RESTRICTION": EventType.ROAD_CLOSURE,
    "FLOW_INCIDENT": EventType.CONGESTION,
    "DISABLED_VEHICLE": EventType.ACCIDENT,
}

_HERE_CRITICALITY_MAP: dict[int, Severity] = {
    0: Severity.LOW,
    1: Severity.LOW,
    2: Severity.MEDIUM,
    3: Severity.HIGH,
}


def _here_event_id(incident_id: str) -> str:
    return hashlib.sha256(f"here_{incident_id}".encode()).hexdigest()[:16]


class HereTransitCollector:
    source_name = "here_transit"

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch_incidents(
        self, client: httpx.AsyncClient, lat: float, lon: float, radius_m: int
    ) -> dict:
        params = {
            "apiKey": self._api_key,
            "in": f"circle:{lat},{lon};r={radius_m}",
            "locationReferencing": "none",
        }
        response = await client.get(HERE_INCIDENTS_URL, params=params)
        response.raise_for_status()
        return response.json()

    def _parse_incident(self, item: dict) -> Event | None:
        try:
            incident_id = item.get("id", "")
            props = item.get("incidentDetails", {})

            incident_type_raw = (props.get("type") or "").upper()
            event_type = _HERE_TYPE_MAP.get(incident_type_raw, EventType.ROAD_CLOSURE)

            criticality = int(props.get("criticality", 0))
            severity = _HERE_CRITICALITY_MAP.get(criticality, Severity.LOW)

            description_obj = props.get("description", {})
            description = (
                description_obj.get("value", "")
                if isinstance(description_obj, dict)
                else str(description_obj)
            )

            location = item.get("location", {})
            anchor = location.get("shape", {}).get("links", [{}])[0]
            points = anchor.get("points", [{}])
            pt = points[0] if points else {}
            lat = float(pt.get("lat", 0))
            lon = float(pt.get("lng", 0))

            if lat == 0 and lon == 0:
                return None

            start_time = props.get("startTime") or props.get("entryTime")
            end_time = props.get("endTime")
            started_at = _parse_dt(start_time) or datetime.now(timezone.utc)
            ends_at = _parse_dt(end_time)

            road_name = location.get("description", {})
            road = (
                road_name.get("value", "")
                if isinstance(road_name, dict)
                else str(road_name)
            )
            title = f"{incident_type_raw.replace('_', ' ').title()} — {road}" if road else incident_type_raw.replace("_", " ").title()

            return Event(
                id=_here_event_id(incident_id),
                source=self.source_name,
                type=event_type,
                title=title,
                description=description,
                lat=lat,
                lon=lon,
                severity=severity,
                status="active",
                started_at=started_at,
                ends_at=ends_at,
                url=None,
                raw=item,
            )
        except Exception:
            logger.exception("HereTransitCollector: failed to parse incident %s", item.get("id"))
            return None

    async def fetch_near(self, lat: float, lon: float, radius_km: float) -> list[Event]:
        if not self._api_key:
            logger.warning("HereTransitCollector: no API key, skipping")
            return []

        radius_m = int(radius_km * 1000)
        async with httpx.AsyncClient(timeout=15.0) as client:
            data = await self._fetch_incidents(client, lat, lon, radius_m)

        results = data.get("results", [])
        events: list[Event] = []
        for item in results:
            event = self._parse_incident(item)
            if event is not None:
                events.append(event)

        logger.info("HereTransitCollector: %d results → %d events", len(results), len(events))
        return events


# ---------------------------------------------------------------------------
# Facade: tries providers in order, stops on first success
# ---------------------------------------------------------------------------

class TransitCollector(BaseCollector):
    """Tries Waze → TomTom → HERE in order, uses first that returns data."""

    source_name = "transit"

    def __init__(self, settings: dict) -> None:
        super().__init__(settings)
        api_keys = settings.get("api_keys", {})
        transit_cfg = settings.get("collectors", {}).get("transit", {})
        provider_order = transit_cfg.get("providers", ["waze", "tomtom", "here"])

        self._providers: list[tuple[str, object]] = []
        for name in provider_order:
            if name == "waze":
                self._providers.append(("waze", WazeCollector()))
            elif name == "tomtom":
                key = api_keys.get("tomtom", "")
                if key:
                    self._providers.append(("tomtom", TomTomCollector(key)))
                else:
                    logger.debug("TransitCollector: TomTom skipped — no API key")
            elif name == "here":
                key = api_keys.get("here", "")
                if key:
                    self._providers.append(("here", HereTransitCollector(key)))
                else:
                    logger.debug("TransitCollector: HERE skipped — no API key")

    async def fetch(self) -> list[Event]:
        loc = self.settings.get("location", {})
        lat = float(loc.get("lat", 0))
        lon = float(loc.get("lon", 0))
        radius_km = float(loc.get("radius_km", 10))

        for name, provider in self._providers:
            try:
                events = await provider.fetch_near(lat, lon, radius_km)
                if events:
                    logger.info("TransitCollector: used provider=%s, events=%d", name, len(events))
                    return events
                logger.debug("TransitCollector: provider=%s returned no data, trying next", name)
            except Exception as exc:
                logger.warning("TransitCollector: provider=%s failed (%s), trying next", name, type(exc).__name__)

        logger.info("TransitCollector: all providers returned no data")
        return []
