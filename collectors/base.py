import logging
from abc import ABC, abstractmethod

from bot.geo import is_within_radius
from models.event import Event

logger = logging.getLogger(__name__)


class BaseCollector(ABC):
    def __init__(self, settings: dict) -> None:
        self.settings = settings

    @property
    @abstractmethod
    def source_name(self) -> str: ...

    @abstractmethod
    async def fetch(self) -> list[Event]: ...

    async def collect(
        self, home_lat: float, home_lon: float, radius_km: float
    ) -> list[Event]:
        try:
            events = await self.fetch()
        except Exception:
            logger.exception("Collector %s fetch failed", self.source_name)
            return []

        result = []
        for event in events:
            try:
                if is_within_radius(event.lat, event.lon, home_lat, home_lon, radius_km):
                    result.append(event)
            except Exception:
                logger.exception("Collector %s geo filter failed for event %s", self.source_name, event.id)
        return result
