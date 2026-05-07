import hashlib
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import defusedxml.ElementTree as ET

import httpx
from bs4 import BeautifulSoup
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from collectors.base import BaseCollector
from models.event import Event, EventType, Severity

logger = logging.getLogger(__name__)

# Odivelas has a working RSS — use it
ODIVELAS_NEWS_RSS = "https://www.cm-odivelas.pt/pages/321.rss"

# Sintra obras page — Joomla server-rendered HTML
SINTRA_OBRAS_URL = "https://cm-sintra.pt/sintra/espaco-publico/intervencoes-no-espaco-publico"

# Amadora editais/avisos — closest to obras notices
AMADORA_EDITAIS_URL = "https://www.cm-amadora.pt/pt/noticias.html"

# Municipality centroids for geo-matching scraped content
_CENTROIDS: dict[str, tuple[float, float]] = {
    "sintra": (38.8029, -9.3817),
    "amadora": (38.7436, -9.2300),
    "odivelas": (38.7952, -9.1850),
}

_OBRAS_KEYWORDS = {
    "obra", "obras", "intervenção", "intervenções", "pavimentação",
    "requalificação", "construção", "reparação", "viaduto", "ponte",
    "estrada", "arruamento", "ciclovia", "passeio", "saneamento",
    "conduta", "tubagem", "calcetamento",
}


def _no_retry_on_429(exc: BaseException) -> bool:
    return "429" not in str(exc)


def _obras_id(source: str, uid: str) -> str:
    return hashlib.sha256(f"obras_{source}_{uid}".encode()).hexdigest()[:16]


def _parse_rss_date(date_str: str) -> datetime:
    try:
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _is_obras_related(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _OBRAS_KEYWORDS)


def _strip_html(html: str) -> str:
    try:
        return BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:
        return html


def _make_obras_event(
    source_name: str,
    uid: str,
    title: str,
    description: str,
    url: str,
    started_at: datetime,
    municipality: str,
) -> Event:
    lat, lon = _CENTROIDS.get(municipality, _CENTROIDS["sintra"])
    return Event(
        id=_obras_id(source_name, uid),
        source=source_name,
        type=EventType.PLANNED_WORKS,
        title=title[:120],
        description=description[:300],
        lat=lat,
        lon=lon,
        severity=Severity.LOW,
        status="active",
        started_at=started_at,
        ends_at=None,
        url=url,
        raw={"title": title, "description": description, "url": url},
    )


class ObrasCollector(BaseCollector):
    """Collects public works notices from Sintra, Amadora, and Odivelas."""

    source_name = "obras"

    # ------------------------------------------------------------------
    # Odivelas — RSS
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch_odivelas_rss(self, client: httpx.AsyncClient) -> list[Event]:
        response = await client.get(ODIVELAS_NEWS_RSS)
        response.raise_for_status()
        root = ET.fromstring(response.text)
        channel = root.find("channel")
        if channel is None:
            return []

        events: list[Event] = []
        for item in channel.findall("item"):
            title = (item.findtext("title") or "").strip()
            description = _strip_html(item.findtext("description") or "")
            link = (item.findtext("link") or ODIVELAS_NEWS_RSS).strip()
            pub_date = item.findtext("pubDate") or ""
            guid = item.findtext("guid") or link

            if not _is_obras_related(f"{title} {description}"):
                continue

            started_at = _parse_rss_date(pub_date)
            events.append(_make_obras_event(
                self.source_name, guid, title, description, link, started_at, "odivelas"
            ))

        return events

    # ------------------------------------------------------------------
    # Sintra — HTML scrape
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch_sintra_html(self, client: httpx.AsyncClient) -> list[Event]:
        response = await client.get(SINTRA_OBRAS_URL, follow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        events: list[Event] = []
        # Joomla article list — items are inside .items-leading or .item or article tags
        articles = soup.select("article, .items-leading .item, .item-page, li.item")
        if not articles:
            # Fallback: grab all <a> with obras keywords in text
            articles = soup.find_all("a", string=lambda t: t and _is_obras_related(t))

        now = datetime.now(timezone.utc)
        for article in articles[:15]:
            title_tag = article.find(["h2", "h3", "h4", "a"])
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            if not _is_obras_related(title):
                continue

            link_tag = article.find("a", href=True)
            url = link_tag["href"] if link_tag else SINTRA_OBRAS_URL
            if url.startswith("/"):
                url = "https://cm-sintra.pt" + url
            if not url.startswith(("http://", "https://")):
                url = SINTRA_OBRAS_URL

            desc_tag = article.find(class_=["article-introtext", "intro", "description"])
            description = desc_tag.get_text(" ", strip=True) if desc_tag else title

            uid = hashlib.md5(title.encode()).hexdigest()[:12]
            events.append(_make_obras_event(
                self.source_name, f"sintra_{uid}", title, description, url, now, "sintra"
            ))

        return events

    # ------------------------------------------------------------------
    # Amadora — HTML scrape (noticias page filtered by obras keywords)
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception(_no_retry_on_429),
        reraise=True,
    )
    async def _fetch_amadora_html(self, client: httpx.AsyncClient) -> list[Event]:
        response = await client.get(AMADORA_EDITAIS_URL, follow_redirects=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        events: list[Event] = []
        now = datetime.now(timezone.utc)

        # Joomla news list — articles in .items or li.item
        items = soup.select(".items .item, li.item, article.item")
        for item in items[:20]:
            title_tag = item.find(["h2", "h3", "h4"])
            if not title_tag:
                continue
            title = title_tag.get_text(strip=True)
            if not _is_obras_related(title):
                continue

            link_tag = item.find("a", href=True)
            url = link_tag["href"] if link_tag else AMADORA_EDITAIS_URL
            if url.startswith("/"):
                url = "https://www.cm-amadora.pt" + url
            if not url.startswith(("http://", "https://")):
                url = AMADORA_EDITAIS_URL

            desc_tag = item.find(class_=["article-introtext", "intro", "description"])
            description = desc_tag.get_text(" ", strip=True) if desc_tag else title

            uid = hashlib.md5(title.encode()).hexdigest()[:12]
            events.append(_make_obras_event(
                self.source_name, f"amadora_{uid}", title, description, url, now, "amadora"
            ))

        return events

    # ------------------------------------------------------------------
    # Main fetch
    # ------------------------------------------------------------------

    async def fetch(self) -> list[Event]:
        import asyncio
        async with httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "PTEvents/1.0"}) as client:
            results = await asyncio.gather(
                self._fetch_odivelas_rss(client),
                self._fetch_sintra_html(client),
                self._fetch_amadora_html(client),
                return_exceptions=True,
            )

        events: list[Event] = []
        labels = ("odivelas_rss", "sintra_html", "amadora_html")
        for label, result in zip(labels, results):
            if isinstance(result, Exception):
                logger.error("ObrasCollector [%s] failed: %s", label, result)
            else:
                events.extend(result)

        logger.info("ObrasCollector: fetched %d obras items", len(events))
        return events
