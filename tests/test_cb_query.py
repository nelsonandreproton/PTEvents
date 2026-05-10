"""Tests for the cb_query callback dispatcher in bot.main."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

from models.event import EventType, Severity


# ---------------------------------------------------------------------------
# Helpers to build fake Telegram objects
# ---------------------------------------------------------------------------

def make_query(data: str, chat_id: int = 123) -> MagicMock:
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_reply_markup = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.chat.id = chat_id
    return query


def make_context(chat_id: int = 123, filters: dict | None = None) -> MagicMock:
    context = MagicMock()
    context.bot_data = {
        "allowed_chat_id": chat_id,
        "settings": {
            "filters": filters if filters is not None else {"min_severity": "LOW", "enabled_types": None},
        },
    }
    return context


def make_update(query) -> MagicMock:
    update = MagicMock()
    update.callback_query = query
    return update


# ---------------------------------------------------------------------------
# Import cb_query after helpers are defined
# ---------------------------------------------------------------------------

from bot.main import cb_query  # noqa: E402


# ---------------------------------------------------------------------------
# Auth guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cb_query_wrong_chat_id_ignored():
    query = make_query("sv:HIGH", chat_id=999)
    context = make_context(chat_id=123)
    update = make_update(query)

    await cb_query(update, context)

    query.answer.assert_awaited_once()
    query.edit_message_text.assert_not_awaited()
    assert context.bot_data["settings"]["filters"]["min_severity"] == "LOW"


# ---------------------------------------------------------------------------
# noop
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cb_query_noop_just_answers():
    query = make_query("noop")
    context = make_context()
    update = make_update(query)

    await cb_query(update, context)

    query.answer.assert_awaited_once()
    query.edit_message_reply_markup.assert_not_awaited()


# ---------------------------------------------------------------------------
# sv: severity
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cb_query_sv_updates_severity(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "filters:\n  min_severity: LOW\n  enabled_types: null\ntelegram:\n  token: '${TOKEN}'\n",
        encoding="utf-8",
    )
    query = make_query("sv:HIGH")
    context = make_context()
    update = make_update(query)

    with patch("bot.main.CONFIG_PATH", yaml_path):
        await cb_query(update, context)

    assert context.bot_data["settings"]["filters"]["min_severity"] == "HIGH"
    query.answer.assert_awaited_once()
    query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_query_sv_invalid_name():
    query = make_query("sv:EXTREME")
    context = make_context()
    update = make_update(query)

    await cb_query(update, context)

    query.answer.assert_awaited_once_with("Severidade inválida")
    assert context.bot_data["settings"]["filters"]["min_severity"] == "LOW"


@pytest.mark.asyncio
async def test_cb_query_sv_same_value_no_crash(tmp_path):
    """Tapping the already-selected severity must not raise (BadRequest swallowed)."""
    from telegram.error import BadRequest

    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "filters:\n  min_severity: HIGH\ntelegram:\n  token: '${TOKEN}'\n",
        encoding="utf-8",
    )
    query = make_query("sv:HIGH")
    context = make_context(filters={"min_severity": "HIGH", "enabled_types": None})
    query.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))
    update = make_update(query)

    with patch("bot.main.CONFIG_PATH", yaml_path):
        await cb_query(update, context)  # must not raise

    query.answer.assert_awaited_once()


# ---------------------------------------------------------------------------
# tt: type toggle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cb_query_tt_toggles_type(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "filters:\n  min_severity: LOW\n  enabled_types: null\ntelegram:\n  token: '${TOKEN}'\n",
        encoding="utf-8",
    )
    query = make_query("tt:0:FIRE")
    context = make_context()
    update = make_update(query)

    with patch("bot.main.CONFIG_PATH", yaml_path):
        await cb_query(update, context)

    enabled = context.bot_data["settings"]["filters"]["enabled_types"]
    assert "FIRE" not in enabled  # was on (None=all), toggled off
    query.answer.assert_awaited_once()
    query.edit_message_reply_markup.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_query_tt_invalid_type_value():
    query = make_query("tt:0:INVALID_TYPE")
    context = make_context()
    update = make_update(query)

    await cb_query(update, context)

    query.answer.assert_awaited_once_with("Tipo inválido")
    # enabled_types must be unchanged
    assert context.bot_data["settings"]["filters"]["enabled_types"] is None


@pytest.mark.asyncio
async def test_cb_query_tt_malformed_payload():
    query = make_query("tt:0")  # only 2 parts, missing type
    context = make_context()
    update = make_update(query)

    await cb_query(update, context)

    query.answer.assert_awaited_once()
    query.edit_message_reply_markup.assert_not_awaited()


# ---------------------------------------------------------------------------
# tx: bulk operations
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cb_query_tx_none_disables_all(tmp_path):
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "filters:\n  min_severity: LOW\ntelegram:\n  token: '${TOKEN}'\n",
        encoding="utf-8",
    )
    query = make_query("tx:none:0")
    context = make_context()
    update = make_update(query)

    with patch("bot.main.CONFIG_PATH", yaml_path):
        await cb_query(update, context)

    assert context.bot_data["settings"]["filters"]["enabled_types"] == []
    query.answer.assert_awaited_once_with("Todos inativos")


@pytest.mark.asyncio
async def test_cb_query_tx_all_enables_all(tmp_path):
    from bot.keyboards import ALL_TYPES

    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "filters:\n  min_severity: LOW\n  enabled_types: []\ntelegram:\n  token: '${TOKEN}'\n",
        encoding="utf-8",
    )
    query = make_query("tx:all:0")
    context = make_context(filters={"min_severity": "LOW", "enabled_types": []})
    update = make_update(query)

    with patch("bot.main.CONFIG_PATH", yaml_path):
        await cb_query(update, context)

    assert len(context.bot_data["settings"]["filters"]["enabled_types"]) == len(ALL_TYPES)
    query.answer.assert_awaited_once_with("Todos ativos")


@pytest.mark.asyncio
async def test_cb_query_tx_all_when_already_all_no_crash(tmp_path):
    """Tapping 'Todos' when already all-enabled swallows BadRequest."""
    from telegram.error import BadRequest

    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "filters:\n  min_severity: LOW\ntelegram:\n  token: '${TOKEN}'\n",
        encoding="utf-8",
    )
    query = make_query("tx:all:0")
    query.edit_message_reply_markup = AsyncMock(
        side_effect=BadRequest("Message is not modified")
    )
    context = make_context()
    update = make_update(query)

    with patch("bot.main.CONFIG_PATH", yaml_path):
        await cb_query(update, context)  # must not raise

    query.answer.assert_awaited_once_with("Todos ativos")


# ---------------------------------------------------------------------------
# tp: page navigation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cb_query_tp_navigates_page():
    query = make_query("tp:1")
    context = make_context()
    update = make_update(query)

    await cb_query(update, context)

    query.edit_message_reply_markup.assert_awaited_once()
    query.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_cb_query_tp_invalid_page_int():
    query = make_query("tp:notanumber")
    context = make_context()
    update = make_update(query)

    await cb_query(update, context)

    query.answer.assert_awaited_once()
    query.edit_message_reply_markup.assert_not_awaited()
