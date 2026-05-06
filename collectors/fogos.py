import hashlib
import logging
from datetime import datetime, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception

from collectors.base import BaseCollector
from models.event import Event, EventType, Severity

logger = logging.getLogger(__name__)

_DATE_FORMAT = "%d-%m-%Y %H:%M"


def _no_retry_on_429(exc: BaseException) -> bool:
    return "429" not in str(exc)


def _parse_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _severity(total: int) -> Severity:
    if total <= 2:
        return Severity.LOW
    if total <= 10:
        return Severity.MEDIUM
    if total <= 30:
        return Severity.HIGH
    return Severity.CRITICAL


def _status(raw_status: str) -> str:
    if raw_status in ("Despacho", "Em curso"):
        return "active"
    if raw_status == "Resolução":
        return "resolving"
    return "active"


def _parse_date(value: str) -> datetime:
    try:
        naive = datetime.strptime(value, _DATE_FORMAT)
        return naive.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _event_id(raw_id: object) -> str:
    return hashlib.sha256(f"fogos_{raw_id}".encode()).hexdigest()[:16]


class FogosCollector(BaseCollector):
    source_name = "fogos"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _get_active_incidents(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get("https://api.fogos.pt/v2/incidents/active")
            response.raise_for_status()
            return response.json().get("data", [])

    async def fetch(self) -> list[Event]:
        items = await self._get_active_incidents()
        events: list[Event] = []

        for item in items:
            try:
                aerial = _parse_int(item.get("aerial", 0))
                terrain = _parse_int(item.get("terrain", 0))
                man = _parse_int(item.get("man", 0))
                total = aerial + terrain + man

                event = Event(
                    id=_event_id(item["id"]),
                    source=self.source_name,
                    type=EventType.FIRE,
                    title=f"Incêndio — {item.get('natureza', '')} em {item.get('location', '')}",
                    description=f"{total} meios ({aerial} aéreos, {terrain} terrestres, {man} operacionais)",
                    lat=float(item["lat"]),
                    lon=float(item["lng"]),
                    severity=_severity(total),
                    status=_status(item.get("status", "")),
                    started_at=_parse_date(item.get("datein", "")),
                    url=f"https://fogos.pt/ocorrencia/{item['id']}",
                    raw=item,
                )
                events.append(event)
            except Exception:
                logger.exception("FogosCollector: failed to parse item %s", item.get("id"))

        logger.info("FogosCollector: fetched %d events", len(events))
        return events
