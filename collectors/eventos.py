import hashlib
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import defusedxml.ElementTree as ET

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from models.event import Event, EventType, Severity

logger = logging.getLogger(__name__)

ODIVELAS_EVENTS_RSS = "https://www.cm-odivelas.pt/pages/322.rss"
EVENTBRITE_SEARCH_URL = "https://www.eventbriteapi.com/v3/events/search/"

_CENTROIDS: dict[str, tuple[float, float]] = {
    "sintra": (38.8029, -9.3817),
    "amadora": (38.7436, -9.2300),
    "odivelas": (38.7952, -9.1850),
}


def _no_retry_on_429(exc: BaseException) -> bool:
    return "429" not in str(exc)


def _event_id(source: str, uid: str) -> str:
    return hashlib.sha256(f"eventos_{source}_{uid}".encode()).hexdigest()[:16]


def _parse_rss_date(date_str: str) -> datetime:
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _parse_iso(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    try:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _strip_html(html: str) -> str:
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:
        return html


class EventosCollector(BaseCollector):
    """Collects planned public events from Odivelas RSS and Eventbrite."""

    source_name = "eventos"

    def __init__(self, settings: dict) -> None:
        super().__init__(settings)
        api_keys = settings.get("api_keys", {})
        self._eventbrite_token = api_keys.get("eventbrite", "")

    # ------------------------------------------------------------------
    # Odivelas events RSS
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch_odivelas_events(self, client: httpx.AsyncClient) -> list[Event]:
        response = await client.get(ODIVELAS_EVENTS_RSS)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        channel = root.find("channel")
        if channel is None:
            return []

        events: list[Event] = []
        lat, lon = _CENTROIDS["odivelas"]

        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            description = _strip_html(item.findtext("description") or "")
            link = (item.findtext("link") or ODIVELAS_EVENTS_RSS).strip()
            pub_date = item.findtext("pubDate") or ""
            guid = item.findtext("guid") or link

            started_at = _parse_rss_date(pub_date)

            events.append(Event(
                id=_event_id("odivelas", guid),
                source=self.source_name,
                type=EventType.EVENT_CLOSURE,
                title=title[:120],
                description=description[:300],
                lat=lat,
                lon=lon,
                severity=Severity.LOW,
                status="active",
                started_at=started_at,
                ends_at=None,
                url=link,
                raw={"title": title, "description": description},
            ))

        return events

    # ------------------------------------------------------------------
    # Eventbrite
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch_eventbrite(
        self, client: httpx.AsyncClient, lat: float, lon: float, radius_km: float
    ) -> list[dict]:
        params = {
            "location.latitude": lat,
            "location.longitude": lon,
            "location.within": f"{int(radius_km)}km",
            "expand": "venue",
            "status": "live",
            "page_size": 50,
        }
        headers = {"Authorization": f"Bearer {self._eventbrite_token}"}
        response = await client.get(EVENTBRITE_SEARCH_URL, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        return data.get("events", [])

    def _parse_eventbrite_event(self, item: dict) -> Event | None:
        try:
            eb_id = item.get("id", "")
            name = item.get("name", {})
            title = (name.get("text", "") if isinstance(name, dict) else str(name)).strip()

            desc_obj = item.get("description", {})
            description = (
                desc_obj.get("text", "") if isinstance(desc_obj, dict) else str(desc_obj)
            ).strip()
            if not description:
                summary = item.get("summary", "")
                description = summary[:300] if summary else title

            url = item.get("url", "")

            start_obj = item.get("start", {})
            start_utc = start_obj.get("utc") if isinstance(start_obj, dict) else None
            started_at = _parse_iso(start_utc) or datetime.now(timezone.utc)

            end_obj = item.get("end", {})
            end_utc = end_obj.get("utc") if isinstance(end_obj, dict) else None
            ends_at = _parse_iso(end_utc)

            venue = item.get("venue") or {}
            address = venue.get("address") or {}
            venue_lat = address.get("latitude") or venue.get("latitude")
            venue_lon = address.get("longitude") or venue.get("longitude")

            if venue_lat and venue_lon:
                lat = float(venue_lat)
                lon = float(venue_lon)
            else:
                loc = self.settings.get("location", {})
                lat = float(loc.get("lat", 38.8097))
                lon = float(loc.get("lon", -9.2518))

            return Event(
                id=_event_id("eventbrite", eb_id),
                source=self.source_name,
                type=EventType.EVENT_CLOSURE,
                title=title[:120],
                description=description[:300],
                lat=lat,
                lon=lon,
                severity=Severity.LOW,
                status="active",
                started_at=started_at,
                ends_at=ends_at,
                url=url,
                raw=item,
            )
        except Exception:
            logger.exception("EventosCollector: failed to parse Eventbrite event %s", item.get("id"))
            return None

    # ------------------------------------------------------------------
    # Main fetch
    # ------------------------------------------------------------------

    async def fetch(self) -> list[Event]:
        import asyncio

        loc = self.settings.get("location", {})
        lat = float(loc.get("lat", 38.8097))
        lon = float(loc.get("lon", -9.2518))
        radius_km = float(loc.get("radius_km", 10))

        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "PTEvents/1.0"}) as client:
            tasks = [self._fetch_odivelas_events(client)]
            if self._eventbrite_token:
                tasks.append(self._fetch_eventbrite(client, lat, lon, radius_km))

            results = await asyncio.gather(*tasks, return_exceptions=True)

        events: list[Event] = []
        labels = ["odivelas_rss", "eventbrite"]

        for label, result in zip(labels, results):
            if isinstance(result, Exception):
                logger.error("EventosCollector [%s] failed: %s", label, result)
                continue
            if label == "eventbrite":
                for item in result:
                    event = self._parse_eventbrite_event(item)
                    if event:
                        events.append(event)
            else:
                events.extend(result)

        logger.info("EventosCollector: fetched %d events", len(events))
        return events
