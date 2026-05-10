import asyncio
import logging
import os
import re
import signal
from collections import defaultdict
from pathlib import Path

import yaml
from telegram import BotCommand, Update
from telegram.error import BadRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from models.db import EventDB
from models.event import Severity
from bot.keyboards import (
    all_types_values,
    build_severity_keyboard,
    build_types_keyboard,
    normalize_enabled_types,
    toggle_type,
)
from bot.notifier import Notifier
from bot.preferences import save_filters
from bot.scheduler import AlertScheduler
from bot.web import start_web_server

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_ENV_VAR_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
DB_PATH = "data/events.db"


def _substitute_env(value: str) -> str:
    m = _ENV_VAR_RE.match(value)
    if m:
        var_name = m.group(1)
        return os.environ.get(var_name, value)
    return value


def _resolve_env_vars(obj):
    if isinstance(obj, dict):
        return {k: _resolve_env_vars(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve_env_vars(item) for item in obj]
    if isinstance(obj, str):
        return _substitute_env(obj)
    return obj


def load_settings() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return _resolve_env_vars(raw)


def _allowed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True only if the message comes from the configured chat."""
    allowed_id = context.bot_data.get("allowed_chat_id", 0)
    return update.effective_chat is not None and update.effective_chat.id == allowed_id


# --- Telegram command handlers ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update, context):
        return
    await update.message.reply_text(
        "Olá! Sou o PTEvents Bot.\n"
        "Vou enviar alertas de eventos locais (IPMA, fogos, etc.).\n\n"
        "Comandos disponíveis:\n"
        "/ping — verificar se estou activo\n"
        "/status — últimos eventos activos\n"
        "/radius <km> — ajustar raio de monitorização (temporário)\n"
        "/types — ativar/desativar tipos de eventos\n"
        "/severity — definir severidade mínima"
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update, context):
        return
    await update.message.reply_text("pong")


async def cmd_radius(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update, context):
        return

    args = context.args or []
    if not args:
        current = context.bot_data["settings"]["location"]["radius_km"]
        await update.message.reply_text(f"Raio atual: {current} km\nUso: /radius <km>")
        return

    try:
        new_radius = float(args[0])
    except ValueError:
        await update.message.reply_text("Valor inválido. Exemplo: /radius 15")
        return

    if not (1 <= new_radius <= 100):
        await update.message.reply_text("Raio deve estar entre 1 e 100 km.")
        return

    context.bot_data["settings"]["location"]["radius_km"] = new_radius
    await update.message.reply_text(
        f"Raio atualizado para {new_radius:.0f} km (até reiniciar)."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update, context):
        return

    db: EventDB = context.bot_data["db"]
    rows = db.get_active(20)

    if not rows:
        await update.message.reply_text("Sem eventos activos de momento.")
        return

    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["source"]].append(row)

    lines = ["Eventos activos (últimos 20):\n"]
    for source, events in grouped.items():
        lines.append(source.upper())
        for e in events:
            lines.append(f"  [{e['severity']}] {e['type']} — {e['started_at'][:16]}")
        lines.append("")

    await update.message.reply_text("\n".join(lines))


def _persist_filters(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Write the in-memory filters dict back to settings.yaml."""
    filters = context.bot_data["settings"].get("filters", {})
    try:
        save_filters(CONFIG_PATH, filters)
    except Exception:
        logger.exception("Failed to persist filter preferences")


async def cmd_types(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update, context):
        return
    filters = context.bot_data["settings"].setdefault("filters", {})
    enabled = normalize_enabled_types(filters.get("enabled_types"))
    await update.message.reply_text(
        "Tipos de eventos (✅ ativo / ❌ inativo):",
        reply_markup=build_types_keyboard(enabled, page=0),
    )


async def cmd_severity(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _allowed(update, context):
        return
    filters = context.bot_data["settings"].setdefault("filters", {})
    name = (filters.get("min_severity") or "LOW").upper()
    try:
        current = Severity[name]
    except KeyError:
        current = Severity.LOW
    await update.message.reply_text(
        f"Severidade mínima atual: {current.value}",
        reply_markup=build_severity_keyboard(current),
    )


async def _try_edit_markup(query, **kwargs) -> None:
    try:
        await query.edit_message_reply_markup(**kwargs)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def _try_edit_text(query, **kwargs) -> None:
    try:
        await query.edit_message_text(**kwargs)
    except BadRequest as exc:
        if "not modified" not in str(exc).lower():
            raise


async def cb_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Single dispatcher for all inline-keyboard taps."""
    query = update.callback_query
    if query is None:
        return
    chat_id = query.message.chat.id if query.message else None
    allowed_id = context.bot_data.get("allowed_chat_id", 0)
    if chat_id != allowed_id:
        await query.answer()
        return

    data = query.data or ""
    settings = context.bot_data["settings"]
    filters = settings.setdefault("filters", {})

    if data == "noop":
        await query.answer()
        return

    if data.startswith("tp:"):
        # Page navigation only — no state change
        try:
            page = int(data.split(":", 1)[1])
        except ValueError:
            await query.answer()
            return
        enabled = normalize_enabled_types(filters.get("enabled_types"))
        await _try_edit_markup(query, reply_markup=build_types_keyboard(enabled, page=page))
        await query.answer()
        return

    if data.startswith("tt:"):
        # Toggle one type. Format: tt:<page>:<TYPE>
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return
        try:
            page = int(parts[1])
        except ValueError:
            page = 0
        type_value = parts[2]
        try:
            new_list = toggle_type(filters.get("enabled_types"), type_value)
        except ValueError:
            await query.answer("Tipo inválido")
            return
        filters["enabled_types"] = new_list
        _persist_filters(context)
        enabled = set(new_list)
        await _try_edit_markup(query, reply_markup=build_types_keyboard(enabled, page=page))
        await query.answer(f"{type_value}: {'on' if type_value in enabled else 'off'}")
        return

    if data.startswith("tx:"):
        # Bulk: tx:all:<page> or tx:none:<page>
        parts = data.split(":", 2)
        if len(parts) != 3:
            await query.answer()
            return
        action = parts[1]
        try:
            page = int(parts[2])
        except ValueError:
            page = 0
        if action == "all":
            filters["enabled_types"] = all_types_values()
            answer = "Todos ativos"
        elif action == "none":
            filters["enabled_types"] = []
            answer = "Todos inativos"
        else:
            await query.answer()
            return
        _persist_filters(context)
        enabled = set(filters["enabled_types"])
        await _try_edit_markup(query, reply_markup=build_types_keyboard(enabled, page=page))
        await query.answer(answer)
        return

    if data.startswith("sv:"):
        name = data.split(":", 1)[1].upper()
        try:
            new_severity = Severity[name]
        except KeyError:
            await query.answer("Severidade inválida")
            return
        filters["min_severity"] = new_severity.value
        _persist_filters(context)
        await _try_edit_text(
            query,
            text=f"Severidade mínima atual: {new_severity.value}",
            reply_markup=build_severity_keyboard(new_severity),
        )
        await query.answer(f"→ {new_severity.value}")
        return

    await query.answer()


# --- Application lifecycle ---

async def _run(settings: dict) -> None:
    db = EventDB(DB_PATH)

    telegram_cfg = settings.get("telegram", {})
    token = telegram_cfg.get("token", "")
    chat_id = telegram_cfg.get("chat_id", "")

    notifier = Notifier(token=token, chat_id=chat_id)
    scheduler = AlertScheduler(settings=settings, db=db, notifier=notifier)

    app = (
        Application.builder()
        .token(token)
        .build()
    )
    app.bot_data["db"] = db
    app.bot_data["settings"] = settings
    try:
        app.bot_data["allowed_chat_id"] = int(chat_id)
    except (ValueError, TypeError):
        app.bot_data["allowed_chat_id"] = 0
        logger.warning("TELEGRAM_CHAT_ID is not a valid integer — all commands will be ignored")

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("radius", cmd_radius))
    app.add_handler(CommandHandler("types", cmd_types))
    app.add_handler(CommandHandler("severity", cmd_severity))
    app.add_handler(CallbackQueryHandler(cb_query))

    stop_event = asyncio.Event()

    def _request_stop(*_):
        logger.info("Shutdown signal received")
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, _request_stop)
        loop.add_signal_handler(signal.SIGINT, _request_stop)
    except (NotImplementedError, AttributeError):
        # Windows: loop.add_signal_handler is not supported
        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

    logger.info("Starting PTEvents bot")
    web_runner = await start_web_server(db, settings)
    async with app:
        await app.start()
        await app.bot.set_my_commands([
            BotCommand("start", "Boas-vindas e lista de comandos"),
            BotCommand("ping", "Verificar se o bot está activo"),
            BotCommand("status", "Eventos activos (últimos 20)"),
            BotCommand("radius", "Ajustar raio de monitorização (temporário)"),
            BotCommand("types", "Ativar/desativar tipos de eventos"),
            BotCommand("severity", "Definir severidade mínima das notificações"),
        ])
        await app.updater.start_polling(drop_pending_updates=True)
        scheduler.start()

        await stop_event.wait()

        logger.info("Stopping scheduler and bot")
        scheduler.stop()
        await app.updater.stop()
        await app.stop()

    await web_runner.cleanup()
    logger.info("PTEvents bot shut down cleanly")


def main() -> None:
    settings = load_settings()
    asyncio.run(_run(settings))


if __name__ == "__main__":
    main()
