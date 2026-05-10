import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_LISBON = ZoneInfo("Europe/Lisbon")

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from collectors.ipma import IpmaCollector
from collectors.fogos import FogosCollector
from collectors.transit import TransitCollector
from collectors.air_quality import AirQualityCollector
from collectors.greves import GrevesCollector
from collectors.obras import ObrasCollector
from collectors.eventos import EventosCollector
from collectors.nasa_firms import NasaFirmsCollector
from models.db import EventDB
from models.event import Severity
from bot.notifier import Notifier
from bot.geo import haversine

logger = logging.getLogger(__name__)

SEVERITY_ORDER = [Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]

_COLLECTOR_CONFIG_KEY: dict[str, str] = {
    "IpmaCollector": "ipma",
    "FogosCollector": "fogos",
    "TransitCollector": "transit",
    "AirQualityCollector": "air_quality",
    "GrevesCollector": "greves",
    "ObrasCollector": "obras",
    "EventosCollector": "eventos",
    "NasaFirmsCollector": "nasa_firms",
}


def _severity_index(severity: Severity) -> int:
    try:
        return SEVERITY_ORDER.index(severity)
    except ValueError:
        return 0


def _in_quiet_hours(start: str, end: str) -> bool:
    """Return True if current Lisbon local time is within quiet hours [start, end).
    Supports midnight wrap-around (e.g. start=22:00, end=06:00).
    """
    now = datetime.now(_LISBON).strftime("%H:%M")
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
                args=[IpmaCollector(self._settings)],
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
                args=[FogosCollector(self._settings)],
                id="fogos",
                name="Fogos collector",
                max_instances=1,
                misfire_grace_time=60,
            )
            logger.info("Scheduled Fogos collector every %d minutes", interval)

        transit_cfg = collectors_cfg.get("transit", {})
        if transit_cfg.get("enabled", False):
            interval = transit_cfg.get("interval_minutes", 3)
            self._scheduler.add_job(
                self._run_collector,
                trigger=IntervalTrigger(minutes=interval),
                args=[TransitCollector(self._settings)],
                id="transit",
                name="Transit collector",
                max_instances=1,
                misfire_grace_time=60,
            )
            logger.info("Scheduled HERE Transit collector every %d minutes", interval)

        air_quality_cfg = collectors_cfg.get("air_quality", {})
        if air_quality_cfg.get("enabled", False):
            interval = air_quality_cfg.get("interval_minutes", 30)
            self._scheduler.add_job(
                self._run_collector,
                trigger=IntervalTrigger(minutes=interval),
                args=[AirQualityCollector(self._settings)],
                id="air_quality",
                name="APA Air Quality collector",
                max_instances=1,
                misfire_grace_time=60,
            )
            logger.info("Scheduled APA Air Quality collector every %d minutes", interval)

        greves_cfg = collectors_cfg.get("greves", {})
        if greves_cfg.get("enabled", False):
            interval = greves_cfg.get("interval_minutes", 60)
            self._scheduler.add_job(
                self._run_collector,
                trigger=IntervalTrigger(minutes=interval),
                args=[GrevesCollector(self._settings)],
                id="greves",
                name="DGERT Greves collector",
                max_instances=1,
                misfire_grace_time=300,
            )
            logger.info("Scheduled Greves collector every %d minutes", interval)

        obras_cfg = collectors_cfg.get("obras", {})
        if obras_cfg.get("enabled", False):
            interval = obras_cfg.get("interval_minutes", 360)
            self._scheduler.add_job(
                self._run_collector,
                trigger=IntervalTrigger(minutes=interval),
                args=[ObrasCollector(self._settings)],
                id="obras",
                name="Obras collector",
                max_instances=1,
                misfire_grace_time=300,
            )
            logger.info("Scheduled Obras collector every %d minutes", interval)

        eventos_cfg = collectors_cfg.get("eventos", {})
        if eventos_cfg.get("enabled", False):
            interval = eventos_cfg.get("interval_minutes", 60)
            self._scheduler.add_job(
                self._run_collector,
                trigger=IntervalTrigger(minutes=interval),
                args=[EventosCollector(self._settings)],
                id="eventos",
                name="Eventos collector",
                max_instances=1,
                misfire_grace_time=300,
            )
            logger.info("Scheduled Eventos collector every %d minutes", interval)

        nasa_firms_cfg = collectors_cfg.get("nasa_firms", {})
        if nasa_firms_cfg.get("enabled", False):
            interval = nasa_firms_cfg.get("interval_minutes", 10)
            self._scheduler.add_job(
                self._run_collector,
                trigger=IntervalTrigger(minutes=interval),
                args=[NasaFirmsCollector(self._settings)],
                id="nasa_firms",
                name="NASA FIRMS collector",
                max_instances=1,
                misfire_grace_time=60,
            )
            logger.info("Scheduled NASA FIRMS collector every %d minutes", interval)

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

        enabled_types = filters.get("enabled_types")
        enabled_types_set: set[str] | None = (
            set(enabled_types) if enabled_types is not None else None
        )

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

        collectors_cfg = self._settings.get("collectors", {})
        collector_name = type(collector).__name__
        config_key = _COLLECTOR_CONFIG_KEY.get(collector_name, "")
        collector_cfg = collectors_cfg.get(config_key, {})
        excluded_types = {t.upper() for t in collector_cfg.get("excluded_types", [])}

        try:
            events = await collector.collect(lat, lon, radius_km)
        except Exception:
            logger.exception("Collector %s failed during collect()", collector_name)
            return

        _CLOSED_KEYWORDS = ("encerrado", "fechado", "resolvido", "closed", "resolved", "encerrada", "fechada")

        for event in events:
            try:
                # Skip excluded event types for this collector
                if excluded_types and event.type in excluded_types:
                    continue

                # Skip closed/resolved events — check both status field and description text
                if event.status not in ("active", "resolving", ""):
                    continue
                desc_lower = (event.description or "").lower()
                if any(kw in desc_lower for kw in _CLOSED_KEYWORDS):
                    continue

                # Hard radius guard (belt-and-suspenders over base.py collect())
                distance_km = haversine(lat, lon, event.lat, event.lon)
                if distance_km > radius_km:
                    continue

                # Type filter (Telegram-only — dashboard sees everything)
                if enabled_types_set is not None and event.type.value not in enabled_types_set:
                    continue

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
                sent = await self._notifier.send_event(event, distance_km, location_name)
                if sent:
                    self._db.mark_notified(event.id)

            except Exception:
                logger.exception(
                    "Error processing event %s from %s", getattr(event, "id", "?"), collector_name
                )

        try:
            self._db.cleanup_expired()
        except Exception:
            logger.exception("cleanup_expired() failed for %s", collector_name)
