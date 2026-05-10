"""Tests for the inline-keyboard helpers (pure functions, no Telegram needed)."""
from __future__ import annotations

from bot.keyboards import (
    ALL_TYPES,
    TOTAL_PAGES,
    TYPES_PER_PAGE,
    all_types_values,
    build_severity_keyboard,
    build_types_keyboard,
    normalize_enabled_types,
    toggle_type,
)
from models.event import EventType, Severity


def test_normalize_none_returns_all_types():
    result = normalize_enabled_types(None)
    assert result == {t.value for t in ALL_TYPES}


def test_normalize_empty_list_returns_empty_set():
    assert normalize_enabled_types([]) == set()


def test_normalize_partial_list_passthrough():
    assert normalize_enabled_types(["FIRE", "ACCIDENT"]) == {"FIRE", "ACCIDENT"}


def test_toggle_type_adds_when_missing():
    result = toggle_type(["FIRE"], "ACCIDENT")
    assert "ACCIDENT" in result
    assert "FIRE" in result


def test_toggle_type_removes_when_present():
    result = toggle_type(["FIRE", "ACCIDENT"], "FIRE")
    assert "FIRE" not in result
    assert "ACCIDENT" in result


def test_toggle_type_from_none_disables_one():
    """None = everything on. Toggling once should disable that one type."""
    result = toggle_type(None, "FIRE")
    assert "FIRE" not in result
    # All other types still present
    assert len(result) == len(ALL_TYPES) - 1


def test_toggle_preserves_enum_order():
    result = toggle_type(["CONGESTION", "FIRE"], "ACCIDENT")
    enum_order = [t.value for t in ALL_TYPES]
    indices = [enum_order.index(v) for v in result]
    assert indices == sorted(indices)


def test_types_keyboard_first_page_buttons():
    enabled = {"FIRE"}
    kb = build_types_keyboard(enabled, page=0)
    # rows: TYPES_PER_PAGE type rows + 1 nav row + 1 bulk row
    assert len(kb.inline_keyboard) == TYPES_PER_PAGE + 2

    fire_row = kb.inline_keyboard[0]
    assert fire_row[0].text.startswith("✅")
    assert fire_row[0].callback_data == "tt:0:FIRE"


def test_types_keyboard_disabled_marker():
    kb = build_types_keyboard(set(), page=0)
    fire_row = kb.inline_keyboard[0]
    assert fire_row[0].text.startswith("❌")


def test_types_keyboard_pagination_first_page_no_prev():
    kb = build_types_keyboard(set(), page=0)
    nav = kb.inline_keyboard[-2]  # second-to-last is nav row
    callbacks = [b.callback_data for b in nav]
    assert not any(c.startswith("tp:") and c.endswith(":") for c in callbacks)
    # No prev button on first page
    prev_buttons = [b for b in nav if b.text.startswith("«")]
    assert prev_buttons == []
    next_buttons = [b for b in nav if b.text.endswith("»")]
    assert len(next_buttons) == 1


def test_types_keyboard_pagination_last_page_no_next():
    kb = build_types_keyboard(set(), page=TOTAL_PAGES - 1)
    nav = kb.inline_keyboard[-2]
    next_buttons = [b for b in nav if b.text.endswith("»")]
    assert next_buttons == []


def test_types_keyboard_invalid_page_clamps_to_zero():
    kb = build_types_keyboard(set(), page=999)
    # First button should be the first type (page 0 behavior)
    first = kb.inline_keyboard[0][0]
    assert first.callback_data.endswith(f":{ALL_TYPES[0].value}")


def test_severity_keyboard_marks_current():
    kb = build_severity_keyboard(Severity.HIGH)
    row = kb.inline_keyboard[0]
    assert len(row) == 4
    high_btn = next(b for b in row if "HIGH" in b.text)
    assert high_btn.text.startswith("▶")
    low_btn = next(b for b in row if "LOW" in b.text)
    assert not low_btn.text.startswith("▶")


def test_severity_keyboard_callbacks():
    kb = build_severity_keyboard(Severity.LOW)
    callbacks = [b.callback_data for b in kb.inline_keyboard[0]]
    assert callbacks == ["sv:LOW", "sv:MEDIUM", "sv:HIGH", "sv:CRITICAL"]


def test_all_types_values_is_29():
    # Sanity check — confirms we expose every EventType
    assert len(all_types_values()) == len(EventType)
