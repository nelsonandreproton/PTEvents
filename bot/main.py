import asyncio
import logging
import os
import re
import signal
from collections import defaultdict
from pathlib import Path

import yaml
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from models.db import EventDB
from bot.notifier import Notifier
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
        "/radius <km> — ajustar raio de monitorização (temporário)"
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
