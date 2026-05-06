import logging
from datetime import datetime, timezone

from telegram import Bot
from telegram.error import TelegramError

from models.event import Event, EventType, Severity

logger = logging.getLogger(__name__)

_SEVERITY_EMOJI: dict[Severity, str] = {
    Severity.LOW: "🔵",
    Severity.MEDIUM: "🟡",
    Severity.HIGH: "🟠",
    Severity.CRITICAL: "🔴",
}

_EVENT_TYPE_LABEL: dict[EventType, str] = {
    EventType.FIRE: "INCÊNDIO",
    EventType.EARTHQUAKE: "SISMO",
    EventType.STORM: "TEMPESTADE",
    EventType.WIND: "VENTO FORTE",
    EventType.RAIN: "CHUVA INTENSA",
    EventType.HEAT: "CALOR EXTREMO",
    EventType.COLD: "FRIO EXTREMO",
    EventType.FLOOD: "INUNDAÇÃO",
    EventType.DROUGHT: "SECA",
    EventType.ACCIDENT: "ACIDENTE",
    EventType.ROAD_CLOSURE: "CORTE DE TRÂNSITO",
    EventType.CONGESTION: "CONGESTIONAMENTO",
    EventType.ROADWORK: "OBRAS NA VIA",
    EventType.POWER_OUTAGE: "CORTE DE ENERGIA",
    EventType.WATER_OUTAGE: "CORTE DE ÁGUA",
    EventType.GAS_LEAK: "FUGA DE GÁS",
    EventType.TELECOM: "FALHA TELECOM",
    EventType.STRIKE: "GREVE",
    EventType.SERVICE_DISRUPTION: "PERTURBAÇÃO",
    EventType.DELAY: "ATRASO",
    EventType.PLANNED_WORKS: "OBRAS PLANEADAS",
    EventType.EVENT_CLOSURE: "EVENTO/ENCERRAMENTO",
    EventType.SCHEDULED_MAINTENANCE: "MANUTENÇÃO",
    EventType.AIR_QUALITY: "QUALIDADE DO AR",
    EventType.FIRE_RISK: "RISCO DE INCÊNDIO",
    EventType.UV_ALERT: "ALERTA UV",
    EventType.CIVIL_PROTECTION: "PROTEÇÃO CIVIL",
    EventType.EVACUATION: "EVACUAÇÃO",
    EventType.TSUNAMI: "TSUNAMI",
    EventType.LANDSLIDE: "DESLIZAMENTO",
}

_PT_MONTHS: dict[int, str] = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr",
    5: "Mai", 6: "Jun", 7: "Jul", 8: "Ago",
    9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}

# Characters that must be escaped in MarkdownV2
_MDV2_ESCAPE = str.maketrans({
    ".": r"\.",
    "!": r"\!",
    "(": r"\(",
    ")": r"\)",
    "-": r"\-",
    "_": r"\_",
    "*": r"\*",
    "[": r"\[",
    "]": r"\]",
    "~": r"\~",
    "`": r"\`",
    ">": r"\>",
    "#": r"\#",
    "+": r"\+",
    "=": r"\=",
    "|": r"\|",
    "{": r"\{",
    "}": r"\}",
})


def _escape(text: str) -> str:
    """Escape a plain string for use inside MarkdownV2 text."""
    return text.translate(_MDV2_ESCAPE)


def _format_timeline(started_at: datetime, ends_at: datetime | None) -> str:
    """Return the timeline line for a notification message."""
    now = datetime.now(timezone.utc)

    # Normalise to UTC-aware if naive
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    elapsed_seconds = (now - started_at).total_seconds()
    if elapsed_seconds < 3600:
        minutes = max(1, int(elapsed_seconds // 60))
        detected_line = _escape(f"Detetado há {minutes} min")
    else:
        detected_line = _escape(f"Desde {started_at.strftime('%H:%M')}")

    if ends_at is None:
        return detected_line

    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)

    month_abbr = _PT_MONTHS[ends_at.month]
    valid_until = _escape(
        f"Válido até {ends_at.day:02d} {month_abbr} {ends_at.strftime('%H:%M')}"
    )
    return f"{detected_line}\n{valid_until}"


def _build_message(event: Event, distance_km: float, location_name: str) -> str:
    """Build a MarkdownV2-formatted alert message."""
    emoji = _SEVERITY_EMOJI.get(event.severity, "⚪")
    type_label = _EVENT_TYPE_LABEL.get(event.type, _escape(event.type.value))
    severity_label = _escape(event.severity.value)

    header = f"{emoji} *{_escape(type_label)} — {severity_label}*"
    location_line = f"📍 {_escape(location_name)} \\({_escape(f'{distance_km:.1f} km de casa')}\\)"
    timeline = _format_timeline(event.started_at, event.ends_at)

    body_lines = [header, location_line, f"🕐 {timeline}", ""]

    if event.description:
        body_lines.append(_escape(event.description))
        body_lines.append("")

    if event.url:
        body_lines.append(f"🔗 {_escape(event.url)}")

    return "\n".join(body_lines).rstrip()


class Notifier:
    """Sends Telegram alert messages for local events."""

    def __init__(self, token: str, chat_id: str) -> None:
        self._bot = Bot(token=token)
        self._chat_id = chat_id

    async def send_event(
        self,
        event: Event,
        distance_km: float,
        location_name: str,
    ) -> bool:
        """Send a formatted event alert. Returns False on any Telegram error."""
        text = _build_message(event, distance_km, location_name)
        return await self.send_text(text)

    async def send_text(self, text: str) -> bool:
        """Send a raw MarkdownV2 message. Returns False on any Telegram error."""
        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=text,
                parse_mode="MarkdownV2",
            )
            return True
        except TelegramError as exc:
            logger.error("Failed to send Telegram message: %s", exc)
            return False
