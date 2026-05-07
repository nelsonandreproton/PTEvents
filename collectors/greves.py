import hashlib
import logging
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from models.event import Event, EventType, Severity

logger = logging.getLogger(__name__)

# DGERT WordPress REST API — category 303 = Greves, 318 = Pré-avisos
DGERT_API_URL = "https://www.dgert.gov.pt/wp-json/wp/v2/posts"
DGERT_GREVES_CATEGORY = 303
DGERT_PREAVISOS_CATEGORY = 318

# Geocentric fallback — greves usually affect regions/sectors, not a point
# Use Lisboa centroid as default (geo-filter will catch it if within radius)
_DEFAULT_LAT = 38.7169
_DEFAULT_LON = -9.1399

# Keywords that raise severity
_HIGH_KEYWORDS = {"transporte", "comboio", "metro", "autocarro", "táxi", "ônibus", "cp ", "metro "}
_CRITICAL_KEYWORDS = {"greve geral", "greve nacional", "serviços mínimos suspensos"}


def _no_retry_on_429(exc: BaseException) -> bool:
    return "429" not in str(exc)


def _greve_id(post_id: int) -> str:
    return hashlib.sha256(f"dgert_{post_id}".encode()).hexdigest()[:16]


def _parse_wp_date(date_str: str) -> datetime:
    """Parse WordPress GMT date string (ISO 8601, no tz suffix = UTC)."""
    try:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def _severity_from_text(text: str) -> Severity:
    lower = text.lower()
    if any(k in lower for k in _CRITICAL_KEYWORDS):
        return Severity.CRITICAL
    if any(k in lower for k in _HIGH_KEYWORDS):
        return Severity.HIGH
    return Severity.MEDIUM


def _strip_html(html: str) -> str:
    try:
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:
        return html


class GrevesCollector(BaseCollector):
    """Polls DGERT WordPress REST API for strike notices."""

    source_name = "greves"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch_category(self, client: httpx.AsyncClient, category_id: int) -> list[dict]:
        params = {
            "categories": category_id,
            "per_page": 20,
            "_fields": "id,title,link,date_gmt,excerpt",
            "orderby": "date",
            "order": "desc",
        }
        response = await client.get(DGERT_API_URL, params=params)
        response.raise_for_status()
        return response.json()

    def _parse_post(self, post: dict) -> Event | None:
        try:
            post_id = post.get("id", 0)
            title_raw = post.get("title", {})
            title = (
                title_raw.get("rendered", "") if isinstance(title_raw, dict) else str(title_raw)
            )
            title = _strip_html(title)

            excerpt_raw = post.get("excerpt", {})
            excerpt = (
                excerpt_raw.get("rendered", "") if isinstance(excerpt_raw, dict) else str(excerpt_raw)
            )
            excerpt = _strip_html(excerpt)

            url = post.get("link", "https://www.dgert.gov.pt")
            date_gmt = post.get("date_gmt", "")
            started_at = _parse_wp_date(date_gmt)

            combined_text = f"{title} {excerpt}"
            severity = _severity_from_text(combined_text)

            loc = self.settings.get("location", {})
            lat = float(loc.get("lat", _DEFAULT_LAT))
            lon = float(loc.get("lon", _DEFAULT_LON))

            return Event(
                id=_greve_id(post_id),
                source=self.source_name,
                type=EventType.STRIKE,
                title=title[:120],
                description=excerpt[:300] if excerpt else title,
                lat=lat,
                lon=lon,
                severity=severity,
                status="active",
                started_at=started_at,
                ends_at=None,
                url=url,
                raw=post,
            )
        except Exception:
            logger.exception("GrevesCollector: failed to parse post %s", post.get("id"))
            return None

    async def fetch(self) -> list[Event]:
        import asyncio
        async with httpx.AsyncClient(timeout=15.0) as client:
            greves, preavisos = await asyncio.gather(
                self._fetch_category(client, DGERT_GREVES_CATEGORY),
                self._fetch_category(client, DGERT_PREAVISOS_CATEGORY),
                return_exceptions=True,
            )

        events: list[Event] = []
        seen_ids: set[str] = set()

        for batch in (greves, preavisos):
            if isinstance(batch, Exception):
                logger.error("GrevesCollector: batch fetch failed: %s", batch)
                continue
            for post in batch:
                event = self._parse_post(post)
                if event and event.id not in seen_ids:
                    seen_ids.add(event.id)
                    events.append(event)

        logger.info("GrevesCollector: fetched %d strike notices", len(events))
        return events
