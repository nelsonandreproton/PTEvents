import asyncio
import hashlib
import logging
from datetime import datetime, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from collectors.base import BaseCollector
from models.event import Event, EventType, Severity

logger = logging.getLogger(__name__)

WARNINGS_URL = "https://api.ipma.pt/open-data/forecast/warnings/warnings_www.json"
SEISMIC_URL = "https://api.ipma.pt/open-data/observation/seismic/sismicidade.json"

# Centroids (lat, lon) by district code
DISTRICT_CENTROIDS: dict[str, tuple[float, float]] = {
    "LIS": (38.7169, -9.1399),
    "POR": (41.1496, -8.6109),
    "SET": (38.5244, -8.8882),
    "FAR": (37.0194, -7.9322),
    "BRG": (41.5454, -8.4265),
    "VCT": (41.6932, -8.8307),
    "AVR": (40.6405, -8.6537),
    "CBR": (40.2033, -8.4103),
    "VIS": (40.6566, -7.9122),
    "GDA": (40.9252, -8.5340),
    "STR": (39.7378, -8.8079),
    "CAS": (39.6636, -8.1338),
    "PGA": (39.4044, -7.8652),
    "EVR": (38.5711, -7.9086),
    "BEJ": (37.9642, -7.8638),
    "PTG": (39.2840, -7.4291),
}
DEFAULT_CENTROID = (39.5, -8.0)

AWARENESS_LEVEL_TO_SEVERITY: dict[str, Severity] = {
    "yellow": Severity.MEDIUM,
    "orange": Severity.HIGH,
    "red": Severity.CRITICAL,
}

AWARENESS_TYPE_TO_EVENT_TYPE: dict[str, EventType] = {
    "Wind": EventType.WIND,
    "Rain": EventType.RAIN,
    "Snow/Ice": EventType.COLD,
    "Extreme temperature": EventType.HEAT,
    "Coastal Event": EventType.FLOOD,
    "Thunderstorm": EventType.STORM,
}


def _sha256_id(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    # Normalise the Z suffix and various ISO formats
    value = value.strip().replace("Z", "+00:00")
    formats = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(value, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    logger.warning("Could not parse datetime: %r", value)
    return None


def _should_retry(exc: BaseException) -> bool:
    return "429" not in str(exc)


def _make_retry() -> retry:
    return retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_should_retry),
        reraise=True,
    )


class IpmaCollector(BaseCollector):
    source_name = "ipma"

    def __init__(self, settings: dict) -> None:
        super().__init__(settings)

    @_make_retry()
    async def _fetch_warnings(self, client: httpx.AsyncClient) -> list[dict]:
        response = await client.get(WARNINGS_URL)
        response.raise_for_status()
        return response.json()

    @_make_retry()
    async def _fetch_seismic(self, client: httpx.AsyncClient) -> list[dict]:
        response = await client.get(SEISMIC_URL)
        response.raise_for_status()
        data = response.json()
        # The API may return a dict with a list, or a bare list
        if isinstance(data, dict):
            for key in ("data", "items", "features"):
                if key in data and isinstance(data[key], list):
                    return data[key]
        return data if isinstance(data, list) else []

    def _parse_warning(self, item: dict) -> Event | None:
        area = item.get("idAreaAviso", "")
        start_time = item.get("startTime", "")
        end_time = item.get("endTime")
        awareness_level = (item.get("awarenessLevelID") or "").lower()
        awareness_type = item.get("awarenessTypeName", "")
        text = item.get("text", "")

        severity = AWARENESS_LEVEL_TO_SEVERITY.get(awareness_level, Severity.LOW)
        event_type = AWARENESS_TYPE_TO_EVENT_TYPE.get(awareness_type, EventType.STORM)

        # Resolve centroid — try exact match then prefix match
        district_key = area.upper()
        centroid = DISTRICT_CENTROIDS.get(district_key)
        if centroid is None:
            for key, coords in DISTRICT_CENTROIDS.items():
                if district_key.startswith(key) or key.startswith(district_key):
                    centroid = coords
                    break
        if centroid is None:
            centroid = DEFAULT_CENTROID
        lat, lon = centroid

        started_at = _parse_datetime(start_time) or datetime.now(timezone.utc)
        ends_at = _parse_datetime(end_time)

        event_id = _sha256_id(f"ipma_warning_{area}{start_time}")
        title = f"Aviso {awareness_type} ({awareness_level.capitalize()}) — {area}"

        return Event(
            id=event_id,
            source=self.source_name,
            type=event_type,
            title=title,
            description=text,
            lat=lat,
            lon=lon,
            severity=severity,
            status="active",
            started_at=started_at,
            ends_at=ends_at,
            url=WARNINGS_URL,
            raw=item,
        )

    def _parse_seismic(self, item: dict) -> Event | None:
        mag = item.get("mag") or item.get("magnitude")
        if mag is None:
            return None
        mag = float(mag)
        if mag < 1.5:
            return None

        lat = float(item.get("lat", 0))
        lon = float(item.get("lon", 0))
        time_str = item.get("time", "")

        if mag < 2.5:
            severity = Severity.LOW
        elif mag < 4.0:
            severity = Severity.MEDIUM
        elif mag < 6.0:
            severity = Severity.HIGH
        else:
            severity = Severity.CRITICAL

        started_at = _parse_datetime(time_str) or datetime.now(timezone.utc)
        event_id = _sha256_id(f"ipma_seismic_{time_str}{lat}{lon}")

        depth = item.get("depth") or item.get("prof")
        depth_str = f", profundidade {depth} km" if depth is not None else ""
        description = f"Sismo magnitude {mag:.1f}{depth_str}."

        return Event(
            id=event_id,
            source=self.source_name,
            type=EventType.EARTHQUAKE,
            title=f"Sismo M{mag:.1f}",
            description=description,
            lat=lat,
            lon=lon,
            severity=severity,
            status="active",
            started_at=started_at,
            ends_at=None,
            url=SEISMIC_URL,
            raw=item,
        )

    async def fetch(self) -> list[Event]:
        async with httpx.AsyncClient(timeout=15.0) as client:
            warnings_result, seismic_result = await asyncio.gather(
                self._fetch_warnings(client),
                self._fetch_seismic(client),
                return_exceptions=True,
            )

        events: list[Event] = []

        if isinstance(warnings_result, Exception):
            logger.error("IPMA warnings fetch failed: %s", warnings_result)
        else:
            for item in warnings_result:
                try:
                    event = self._parse_warning(item)
                    if event is not None:
                        events.append(event)
                except Exception:
                    logger.exception("Failed to parse IPMA warning item: %r", item)

        if isinstance(seismic_result, Exception):
            logger.error("IPMA seismic fetch failed: %s", seismic_result)
        else:
            for item in seismic_result:
                try:
                    event = self._parse_seismic(item)
                    if event is not None:
                        events.append(event)
                except Exception:
                    logger.exception("Failed to parse IPMA seismic item: %r", item)

        logger.info(
            "IPMA collected %d events (%d warnings source, %d seismic source)",
            len(events),
            0 if isinstance(warnings_result, Exception) else len(warnings_result),
            0 if isinstance(seismic_result, Exception) else len(seismic_result),
        )
        return events
