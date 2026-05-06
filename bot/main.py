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

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

_ENV_VAR_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"
DB_PATH = "data/events.db"


def _substitute_env(value: str) -> str:
    """Replace exact '${VAR}' string with os.environ value."""
    m = _ENV_VAR_RE.match(value)
    if m:
        var_name = m.group(1)
        return os.environ.get(var_name, value)
    return value


def _resolve_env_vars(obj):
    """Recursively walk settings and substitute ${VAR} in string values."""
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


# --- Telegram command handlers ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Olá! Sou o PTEvents Bot.\n"
        "Vou enviar alertas de eventos locais (IPMA, fogos, etc.).\n\n"
        "Comandos disponíveis:\n"
        "/ping — verificar se estou activo\n"
        "/status — últimos eventos activos"
    )


async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("pong")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    db: EventDB = context.bot_data["db"]
    rows = db.get_active(20)

    if not rows:
        await update.message.reply_text("Sem eventos activos de momento.")
        return

    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        grouped[row["source"]].append(row)

    lines = ["*Eventos activos (últimos 20):*\n"]
    for source, events in grouped.items():
        lines.append(f"*{source}*")
        for e in events:
            lines.append(
                f"  • [{e['severity']}] {e['type']} — {e['started_at']}"
            )
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


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

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("status", cmd_status))

    stop_event = asyncio.Event()

    def _request_stop(*_):
        logger.info("Shutdown signal received")
        stop_event.set()

    # Register SIGTERM/SIGINT — fallback signal.signal for Windows
    try:
        loop = asyncio.get_event_loop()
        loop.add_signal_handler(signal.SIGTERM, _request_stop)
        loop.add_signal_handler(signal.SIGINT, _request_stop)
    except (NotImplementedError, AttributeError):
        # Windows: loop.add_signal_handler is not supported
        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

    logger.info("Starting PTEvents bot")
    async with app:
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        scheduler.start()

        await stop_event.wait()

        logger.info("Stopping scheduler and bot")
        scheduler.stop()
        await app.updater.stop()
        await app.stop()

    logger.info("PTEvents bot shut down cleanly")


def main() -> None:
    settings = load_settings()
    asyncio.run(_run(settings))


if __name__ == "__main__":
    main()
