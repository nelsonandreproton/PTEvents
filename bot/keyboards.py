"""Inline-keyboard builders for /types and /severity commands.

Pure functions — kept separate from main.py so they can be unit-tested
without spinning up a Telegram Application.
"""
from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from models.event import EventType, Severity

# Keep the same labels the notifier already uses, so the bot's UI stays consistent.
EVENT_TYPE_LABEL: dict[EventType, str] = {
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

ALL_TYPES: list[EventType] = [t for t in EventType if t in EVENT_TYPE_LABEL]
TYPES_PER_PAGE = 10
TOTAL_PAGES = (len(ALL_TYPES) + TYPES_PER_PAGE - 1) // TYPES_PER_PAGE


def normalize_enabled_types(enabled_types: list[str] | None) -> set[str]:
    """`None` means everything enabled — flatten to a concrete set for UI."""
    if enabled_types is None:
        return {t.value for t in ALL_TYPES}
    return set(enabled_types)


def build_types_keyboard(enabled: set[str], page: int = 0) -> InlineKeyboardMarkup:
    """One toggle button per type, paginated. ✅/❌ shows current state."""
    if page < 0 or page >= TOTAL_PAGES:
        page = 0

    start = page * TYPES_PER_PAGE
    end = start + TYPES_PER_PAGE
    page_types = ALL_TYPES[start:end]

    rows: list[list[InlineKeyboardButton]] = []
    for event_type in page_types:
        marker = "✅" if event_type.value in enabled else "❌"
        label = f"{marker} {EVENT_TYPE_LABEL[event_type]}"
        rows.append([
            InlineKeyboardButton(label, callback_data=f"tt:{page}:{event_type.value}")
        ])

    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Anterior", callback_data=f"tp:{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{TOTAL_PAGES}", callback_data="noop"))
    if page < TOTAL_PAGES - 1:
        nav.append(InlineKeyboardButton("Seguinte »", callback_data=f"tp:{page + 1}"))
    rows.append(nav)

    rows.append([
        InlineKeyboardButton("✅ Todos", callback_data=f"tx:all:{page}"),
        InlineKeyboardButton("❌ Nenhum", callback_data=f"tx:none:{page}"),
    ])

    return InlineKeyboardMarkup(rows)


_SEVERITY_BUTTON: dict[Severity, str] = {
    Severity.LOW: "🔵 LOW",
    Severity.MEDIUM: "🟡 MEDIUM",
    Severity.HIGH: "🟠 HIGH",
    Severity.CRITICAL: "🔴 CRITICAL",
}


def build_severity_keyboard(current: Severity) -> InlineKeyboardMarkup:
    """4 buttons in one row. Current selection prefixed with ▶."""
    row: list[InlineKeyboardButton] = []
    for severity in (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL):
        label = _SEVERITY_BUTTON[severity]
        if severity == current:
            label = f"▶ {label}"
        row.append(InlineKeyboardButton(label, callback_data=f"sv:{severity.value}"))
    return InlineKeyboardMarkup([row])


_VALID_TYPE_VALUES: frozenset[str] = frozenset(t.value for t in EventType)


def toggle_type(enabled_types: list[str] | None, type_value: str) -> list[str]:
    """Flip one type. Always returns a list (callers persist a concrete state).

    Raises ValueError for unknown type_value — callers must validate before persisting.
    """
    if type_value not in _VALID_TYPE_VALUES:
        raise ValueError(f"Unknown event type: {type_value!r}")
    current = normalize_enabled_types(enabled_types)
    if type_value in current:
        current.discard(type_value)
    else:
        current.add(type_value)
    # Stable order: match ALL_TYPES enum order
    return [t.value for t in ALL_TYPES if t.value in current]


def all_types_values() -> list[str]:
    return [t.value for t in ALL_TYPES]
