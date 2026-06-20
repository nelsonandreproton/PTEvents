import asyncio
import logging
import os
import re
import signal
from pathlib import Path

import yaml

from models.db import EventDB
from bot.notifier import Notifier
from bot.preferences import apply_overrides, load_filter_overrides
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
# Runtime filter overrides live in the data volume, NOT in the git-tracked
# config/ dir — so the homeserver deploy.sh (`git reset --hard`) can't wipe them.
FILTER_PREFS_PATH = Path("data") / "filter_prefs.yaml"


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
    settings = _resolve_env_vars(raw)

    # Layer runtime filter overrides (min_severity, enabled_types) from the
    # data volume on top of the tracked base filters. quiet_hours and friends
    # still come from settings.yaml, so deploy edits to them keep propagating.
    overrides = load_filter_overrides(FILTER_PREFS_PATH)
    if overrides:
        base_filters = settings.get("filters", {})
        settings["filters"] = apply_overrides(base_filters, overrides)
        logger.info("Applied filter overrides from %s: %s", FILTER_PREFS_PATH, overrides)

    return settings


async def _run(settings: dict) -> None:
    db = EventDB(DB_PATH)

    telegram_cfg = settings.get("telegram", {})
    token = telegram_cfg.get("token", "")
    chat_id = telegram_cfg.get("chat_id", "")

    notifier = Notifier(token=token, chat_id=chat_id)
    scheduler = AlertScheduler(settings=settings, db=db, notifier=notifier)

    stop_event = asyncio.Event()

    def _request_stop(*_):
        logger.info("Shutdown signal received")
        stop_event.set()

    try:
        loop = asyncio.get_running_loop()
        loop.add_signal_handler(signal.SIGTERM, _request_stop)
        loop.add_signal_handler(signal.SIGINT, _request_stop)
    except (NotImplementedError, AttributeError):
        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

    port = int(os.environ.get("PORT", 8080))
    logger.info("Starting PTEvents (scheduler + web, no Telegram bot) on port %d", port)
    web_runner = await start_web_server(db, settings, port=port, prefs_path=FILTER_PREFS_PATH)
    scheduler.start()

    await stop_event.wait()

    logger.info("Stopping scheduler")
    scheduler.stop()
    await web_runner.cleanup()
    logger.info("PTEvents shut down cleanly")


def main() -> None:
    settings = load_settings()
    asyncio.run(_run(settings))


if __name__ == "__main__":
    main()
