import hashlib
import logging
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_LISBON = ZoneInfo("Europe/Lisbon")

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from models.event import Event, EventType, Severity

logger = logging.getLogger(__name__)

QUALAR_BASE = "https://qualar.apambiente.pt/api/app.php"

# IQAr index 1-5 → Severity (1=Muito Bom, 5=Mau)
_IQAR_SEVERITY: dict[int, Severity] = {
    1: Severity.LOW,
    2: Severity.LOW,
    3: Severity.MEDIUM,
    4: Severity.HIGH,
    5: Severity.CRITICAL,
}

# IQAr index 1-5 → Portuguese label
_IQAR_LABEL: dict[int, str] = {
    1: "Muito Bom",
    2: "Bom",
    3: "Médio",
    4: "Fraco",
    5: "Mau",
}

# Only alert when index >= this threshold
_MIN_ALERT_INDEX = 3


def _no_retry_on_429(exc: BaseException) -> bool:
    return "429" not in str(exc)


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def _station_event_id(station_id: int, date_str: str) -> str:
    raw = f"apa_iqar_{station_id}_{date_str}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class AirQualityCollector(BaseCollector):
    """Collects air quality index from APA QualAr stations near home."""

    source_name = "air_quality"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch_map(self, client: httpx.AsyncClient, date_str: str) -> dict:
        params = {"type": "map", "data": date_str, "poluente_id": 0, "en": 0}
        response = await client.get(QUALAR_BASE, params=params)
        response.raise_for_status()
        return response.json()

    async def fetch(self) -> list[Event]:
        loc = self.settings.get("location", {})
        home_lat = float(loc.get("lat", 0))
        home_lon = float(loc.get("lon", 0))
        radius_km = float(loc.get("radius_km", 10))

        # Use double the radius to find nearby stations — geo filter in BaseCollector
        # will then apply the exact radius check
        search_radius_km = radius_km * 2

        today = datetime.now(_LISBON).strftime("%Y-%m-%d")

        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                data = await self._fetch_map(client, today)
            except Exception:
                logger.exception("AirQualityCollector: fetch failed")
                return []

        stations = data.get("stations", [])
        events: list[Event] = []

        for station in stations:
            try:
                s_lat = float(station.get("latitude") or station.get("lat") or 0)
                s_lon = float(station.get("longitude") or station.get("lng") or 0)

                if s_lat == 0 and s_lon == 0:
                    continue

                dist = _haversine_km(home_lat, home_lon, s_lat, s_lon)
                if dist > search_radius_km:
                    continue

                iqar_index = int(station.get("indice") or 0)
                if iqar_index < _MIN_ALERT_INDEX:
                    continue

                severity = _IQAR_SEVERITY.get(iqar_index, Severity.LOW)
                label = _IQAR_LABEL.get(iqar_index, str(iqar_index))
                station_name = station.get("estacao_nome", "Desconhecida")
                dominant_pol = station.get("pols", "")
                val_str = station.get("vals", "")

                description_parts = [f"Índice IQAr: {label}"]
                if dominant_pol:
                    description_parts.append(f"Poluente dominante: {dominant_pol}")
                if val_str:
                    description_parts.append(f"Valor: {val_str}")

                event = Event(
                    id=_station_event_id(station.get("estacao_id", 0), today),
                    source=self.source_name,
                    type=EventType.AIR_QUALITY,
                    title=f"Qualidade do Ar — {label} em {station_name}",
                    description=". ".join(description_parts) + ".",
                    lat=s_lat,
                    lon=s_lon,
                    severity=severity,
                    status="active",
                    started_at=datetime.now(timezone.utc),
                    ends_at=None,
                    url="https://qualar.apambiente.pt",
                    raw=station,
                )
                events.append(event)
            except Exception:
                logger.exception(
                    "AirQualityCollector: failed to parse station %s",
                    station.get("estacao_id"),
                )

        logger.info(
            "AirQualityCollector: %d stations checked, %d alerts generated",
            len(stations),
            len(events),
        )
        return events
