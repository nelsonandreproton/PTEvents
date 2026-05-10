"""Lightweight aiohttp web server serving the PTEvents dashboard."""
import json
import logging
import mimetypes
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "web"
UTC = timezone.utc


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def _build_app(db, settings: dict, config_path: Path | None = None) -> web.Application:
    app = web.Application()
    app["db"] = db
    app["settings"] = settings
    app["config_path"] = config_path
    app.router.add_get("/ptevents/api/events", _handle_events)
    app.router.add_delete("/ptevents/api/events/{event_id}", _handle_dismiss)
    app.router.add_get("/ptevents/api/filters", _handle_get_filters)
    app.router.add_put("/ptevents/api/filters", _handle_put_filters)
    app.router.add_get("/ptevents/api/status", _handle_api_status)
    app.router.add_get("/ptevents/{path:.*}", _handle_static)
    app.router.add_get("/ptevents", _handle_index)
    return app


async def _handle_events(request: web.Request) -> web.Response:
    db = request.app["db"]
    settings = request.app["settings"]
    loc = settings.get("location", {})

    rows = db.get_active_full(200)
    events = []
    for row in rows:
        d = _row_to_dict(row)
        events.append({
            "id": d["id"],
            "source": d["source"],
            "type": d["type"],
            "severity": d["severity"],
            "started_at": d["started_at"],
            "expires_at": d["expires_at"],
            "title": d.get("title") or "",
            "description": d.get("description") or "",
            "lat": d.get("lat"),
            "lon": d.get("lon"),
            "url": d.get("url"),
            "status": d.get("status") or "active",
        })

    payload = {
        "events": events,
        "location": {
            "lat": loc.get("lat", 0),
            "lon": loc.get("lon", 0),
            "name": loc.get("name", ""),
            "radius_km": loc.get("radius_km", 10),
        },
        "generated_at": datetime.now(UTC).isoformat(),
    }
    return web.Response(
        text=json.dumps(payload, ensure_ascii=False),
        content_type="application/json",
    )


async def _handle_get_filters(request: web.Request) -> web.Response:
    filters = request.app["settings"].get("filters", {})
    return web.Response(
        text=json.dumps(filters, ensure_ascii=False),
        content_type="application/json",
    )


async def _handle_put_filters(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="Invalid JSON")

    settings = request.app["settings"]
    filters = settings.setdefault("filters", {})

    if "min_severity" in body:
        filters["min_severity"] = str(body["min_severity"])
    if "enabled_types" in body:
        v = body["enabled_types"]
        filters["enabled_types"] = list(v) if v is not None else None

    config_path: Path | None = request.app.get("config_path")
    if config_path:
        from bot.preferences import save_filters
        try:
            save_filters(config_path, filters)
        except Exception:
            logger.exception("Failed to persist filters via API")

    return web.Response(
        text=json.dumps({"ok": True, "filters": filters}, ensure_ascii=False),
        content_type="application/json",
    )


async def _handle_api_status(request: web.Request) -> web.Response:
    db = request.app["db"]
    rows = db.get_active(200)
    filters = request.app["settings"].get("filters", {})
    return web.Response(
        text=json.dumps({
            "active_events": len(rows),
            "min_severity": filters.get("min_severity", "LOW"),
            "enabled_types": filters.get("enabled_types"),
        }, ensure_ascii=False),
        content_type="application/json",
    )


async def _handle_dismiss(request: web.Request) -> web.Response:
    event_id = request.match_info["event_id"]
    db = request.app["db"]
    db.dismiss(event_id)
    return web.Response(text=json.dumps({"ok": True}), content_type="application/json")


async def _handle_index(request: web.Request) -> web.Response:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise web.HTTPNotFound()
    return web.Response(text=index.read_text(encoding="utf-8"), content_type="text/html")


async def _handle_static(request: web.Request) -> web.Response:
    path = request.match_info["path"]
    if not path or path == "/":
        return await _handle_index(request)

    file_path = (STATIC_DIR / path).resolve()
    # Prevent path traversal
    try:
        file_path.relative_to(STATIC_DIR.resolve())
    except ValueError:
        raise web.HTTPForbidden()

    if not file_path.exists() or not file_path.is_file():
        raise web.HTTPNotFound()

    mime, _ = mimetypes.guess_type(str(file_path))
    mime = mime or "application/octet-stream"
    if mime.startswith("text/"):
        return web.Response(text=file_path.read_text(encoding="utf-8"), content_type=mime)
    return web.Response(body=file_path.read_bytes(), content_type=mime)


async def start_web_server(
    db,
    settings: dict,
    host: str = "0.0.0.0",
    port: int = 8080,
    config_path: Path | None = None,
) -> web.AppRunner:
    app = _build_app(db, settings, config_path=config_path)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Web dashboard running on http://%s:%d/ptevents", host, port)
    return runner
