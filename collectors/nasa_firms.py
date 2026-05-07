import csv
import hashlib
import io
import logging
import math
from datetime import datetime, timezone

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from models.event import Event, EventType, Severity

logger = logging.getLogger(__name__)

# VIIRS S-NPP Near Real-Time — 1-day lookback, CSV format
FIRMS_CSV_URL = "https://firms.modaps.eosdis.nasa.gov/api/area/csv/{api_key}/VIIRS_SNPP_NRT/{bbox}/1"

# Brightness temperature thresholds for fire confidence
# VIIRS confidence: 'l' (low), 'n' (nominal), 'h' (high)
_CONFIDENCE_SEVERITY: dict[str, Severity] = {
    "l": Severity.LOW,
    "n": Severity.MEDIUM,
    "h": Severity.HIGH,
}

# FRP (fire radiative power, MW) upgrade threshold
_FRP_CRITICAL_MW = 100.0


def _no_retry_on_429(exc: BaseException) -> bool:
    return "429" not in str(exc)


def _fire_id(lat: float, lon: float, acq_date: str, acq_time: str) -> str:
    raw = f"firms_{lat:.4f}_{lon:.4f}_{acq_date}_{acq_time}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _bbox_str(lat: float, lon: float, radius_km: float) -> str:
    delta_lat = radius_km / 111.0
    delta_lon = radius_km / (111.0 * math.cos(math.radians(lat)))
    west = lon - delta_lon
    south = lat - delta_lat
    east = lon + delta_lon
    north = lat + delta_lat
    return f"{west:.4f},{south:.4f},{east:.4f},{north:.4f}"


def _parse_firms_datetime(acq_date: str, acq_time: str) -> datetime:
    """Parse FIRMS acquisition date (YYYY-MM-DD) and time (HHMM) to UTC datetime."""
    try:
        time_str = acq_time.zfill(4)
        dt_str = f"{acq_date} {time_str[:2]}:{time_str[2:]}:00"
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


class NasaFirmsCollector(BaseCollector):
    """Satellite fire detection via NASA FIRMS VIIRS S-NPP NRT."""

    source_name = "nasa_firms"

    def __init__(self, settings: dict) -> None:
        super().__init__(settings)
        api_keys = settings.get("api_keys", {})
        self._api_key = api_keys.get("nasa_firms", "")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch_csv(self, client: httpx.AsyncClient, bbox: str) -> str:
        url = FIRMS_CSV_URL.format(api_key=self._api_key, bbox=bbox)
        response = await client.get(url)
        response.raise_for_status()
        return response.text

    def _parse_csv_row(self, row: dict) -> Event | None:
        try:
            lat = float(row.get("latitude", 0))
            lon = float(row.get("longitude", 0))
            if lat == 0 and lon == 0:
                return None

            acq_date = row.get("acq_date", "")
            acq_time = row.get("acq_time", "")
            started_at = _parse_firms_datetime(acq_date, acq_time)

            confidence_raw = (row.get("confidence") or "n").lower().strip()
            severity = _CONFIDENCE_SEVERITY.get(confidence_raw, Severity.MEDIUM)

            frp_raw = row.get("frp", "0")
            try:
                frp = float(frp_raw)
            except (ValueError, TypeError):
                frp = 0.0

            if frp >= _FRP_CRITICAL_MW:
                severity = Severity.CRITICAL

            bright_t31 = row.get("bright_t31", "")
            description_parts = [f"Deteção VIIRS satélite (confiança: {confidence_raw.upper()})"]
            if frp > 0:
                description_parts.append(f"FRP: {frp:.1f} MW")
            if bright_t31:
                description_parts.append(f"Temp. brilho: {bright_t31} K")

            return Event(
                id=_fire_id(lat, lon, acq_date, acq_time),
                source=self.source_name,
                type=EventType.FIRE,
                title=f"Foco de calor detetado por satélite ({acq_date})",
                description=". ".join(description_parts) + ".",
                lat=lat,
                lon=lon,
                severity=severity,
                status="active",
                started_at=started_at,
                ends_at=None,
                url="https://firms.modaps.eosdis.nasa.gov/map/",
                raw=dict(row),
            )
        except Exception:
            logger.exception("NasaFirmsCollector: failed to parse row %s", row)
            return None

    async def fetch(self) -> list[Event]:
        if not self._api_key:
            logger.warning("NasaFirmsCollector: NASA_FIRMS_KEY not set, skipping")
            return []

        loc = self.settings.get("location", {})
        lat = float(loc.get("lat", 0))
        lon = float(loc.get("lon", 0))
        radius_km = float(loc.get("radius_km", 10))
        bbox = _bbox_str(lat, lon, radius_km)

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                csv_text = await self._fetch_csv(client, bbox)
            except Exception as exc:
                # Avoid logging the full URL (contains API key in path)
                logger.error("NasaFirmsCollector: fetch failed: %s", type(exc).__name__)
                return []

        # Empty response or just header = no detections
        lines = csv_text.strip().splitlines()
        if len(lines) <= 1:
            logger.info("NasaFirmsCollector: no fire detections in bbox")
            return []

        reader = csv.DictReader(io.StringIO(csv_text))
        events: list[Event] = []
        for row in reader:
            event = self._parse_csv_row(row)
            if event is not None:
                events.append(event)

        logger.info("NasaFirmsCollector: %d fire detections", len(events))
        return events
