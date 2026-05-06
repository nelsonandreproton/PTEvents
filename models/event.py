from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class EventType(str, Enum):
    FIRE = "FIRE"
    CIVIL_PROTECTION = "CIVIL_PROTECTION"
    EVACUATION = "EVACUATION"
    STORM = "STORM"
    WIND = "WIND"
    RAIN = "RAIN"
    HEAT = "HEAT"
    COLD = "COLD"
    FLOOD = "FLOOD"
    DROUGHT = "DROUGHT"
    EARTHQUAKE = "EARTHQUAKE"
    TSUNAMI = "TSUNAMI"
    LANDSLIDE = "LANDSLIDE"
    ACCIDENT = "ACCIDENT"
    ROAD_CLOSURE = "ROAD_CLOSURE"
    CONGESTION = "CONGESTION"
    ROADWORK = "ROADWORK"
    POWER_OUTAGE = "POWER_OUTAGE"
    WATER_OUTAGE = "WATER_OUTAGE"
    GAS_LEAK = "GAS_LEAK"
    TELECOM = "TELECOM"
    STRIKE = "STRIKE"
    SERVICE_DISRUPTION = "SERVICE_DISRUPTION"
    DELAY = "DELAY"
    PLANNED_WORKS = "PLANNED_WORKS"
    EVENT_CLOSURE = "EVENT_CLOSURE"
    SCHEDULED_MAINTENANCE = "SCHEDULED_MAINTENANCE"
    AIR_QUALITY = "AIR_QUALITY"
    FIRE_RISK = "FIRE_RISK"
    UV_ALERT = "UV_ALERT"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class Event:
    id: str
    source: str
    type: EventType
    title: str
    description: str
    lat: float
    lon: float
    severity: Severity
    status: str
    started_at: datetime
    ends_at: datetime | None = None
    url: str | None = None
    raw: dict = field(default_factory=dict)
