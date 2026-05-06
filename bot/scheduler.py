import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from collectors.ipma import IpmaCollector
from collectors.fogos import FogosCollector
from models.db import EventDB
from models.event import Severity
from bot.notifier import Notifier
from bot.geo import haversine

logger = logging.getLogger(__name__)

SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]


def _severity_index(severity: Severity) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return 0


def _in_quiet_hours(start: str, end: str) -> bool:
    """Return True if current UTC time is within quiet hours [start, end).
    Supports midnight wrap-around (e.g. start=22:00, end=06:00).
    """
    now = datetime.now(timezone.utc).strftime("%H:%M")
    if start <= end:
        return start <= now < end
    # Wraps midnight
    return now >= start or now < end


class AlertScheduler:
    def __init__(self, settings: dict, db: EventDB, notifier: Notifier):
        self._settings = settings
        self._db = db
        self._notifier = notifier
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        loc = self._settings["location"]
        collectors_cfg = self._settings.get("collectors", {})

        ipma_cfg = collectors_cfg.get("ipma", {})
        if ipma_cfg.get("enabled", False):
            interval = ipma_cfg.get("interval_minutes", 10)
            self._scheduler.add_job(
                self._run_collector,
                trigger=IntervalTrigger(minutes=interval),
                args=[IpmaCollector()],
                id="ipma",
                name="IPMA collector",
                max_instances=1,
                misfire_grace_time=60,
            )
            logger.info("Scheduled IPMA collector every %d minutes", interval)

        fogos_cfg = collectors_cfg.get("fogos", {})
        if fogos_cfg.get("enabled", False):
            interval = fogos_cfg.get("interval_minutes", 5)
            self._scheduler.add_job(
                self._run_collector,
                trigger=IntervalTrigger(minutes=interval),
                args=[FogosCollector()],
                id="fogos",
                name="Fogos collector",
                max_instances=1,
                misfire_grace_time=60,
            )
            logger.info("Scheduled Fogos collector every %d minutes", interval)

        self._scheduler.start()
        logger.info("AlertScheduler started")

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("AlertScheduler stopped")

    async def _run_collector(self, collector) -> None:
        loc = self._settings["location"]
        lat = loc["lat"]
        lon = loc["lon"]
        radius_km = loc["radius_km"]
        location_name = loc.get("name", "")

        filters = self._settings.get("filters", {})
        min_severity_name = filters.get("min_severity", "LOW")
        try:
            min_severity = Severity[min_severity_name.upper()]
        except KeyError:
            min_severity = Severity.LOW
        min_severity_idx = _severity_index(min_severity)

        quiet_cfg = filters.get("quiet_hours", {})
        quiet_enabled = quiet_cfg.get("enabled", False)
        quiet_start = quiet_cfg.get("start", "23:00")
        quiet_end = quiet_cfg.get("end", "07:00")
        except_severity_name = quiet_cfg.get("except_severity", "CRITICAL")
        try:
            except_severity = Severity[except_severity_name.upper()]
        except KeyError:
            except_severity = Severity.CRITICAL
        except_severity_idx = _severity_index(except_severity)

        collector_name = type(collector).__name__
        try:
            events = await collector.collect(lat, lon, radius_km)
        except Exception:
            logger.exception("Collector %s failed during collect()", collector_name)
            return

        for event in events:
            try:
                # Severity filter
                event_severity_idx = _severity_index(event.severity)
                if event_severity_idx < min_severity_idx:
                    continue

                # Quiet hours filter
                if quiet_enabled and _in_quiet_hours(quiet_start, quiet_end):
                    if event_severity_idx < except_severity_idx:
                        logger.debug(
                            "Quiet hours: suppressing event %s (severity %s)",
                            event.id,
                            event.severity,
                        )
                        continue

                if not self._db.is_new(event):
                    continue

                self._db.save(event)

                distance_km = haversine(lat, lon, event.lat, event.lon)
                await self._notifier.send_event(event, distance_km, location_name)

            except Exception:
                logger.exception(
                    "Error processing event %s from %s", getattr(event, "id", "?"), collector_name
                )

        try:
            self._db.cleanup_expired()
        except Exception:
            logger.exception("cleanup_expired() failed for %s", collector_name)
