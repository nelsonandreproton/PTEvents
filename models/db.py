import sqlite3
from datetime import datetime, timedelta, timezone
from models.event import Event, EventType

UTC = timezone.utc

TTL_HOURS: dict[str, int] = {
    EventType.FIRE: 2,
    EventType.STORM: 6,
    EventType.EARTHQUAKE: 24,
    EventType.ACCIDENT: 1,
    EventType.ROAD_CLOSURE: 4,
    EventType.POWER_OUTAGE: 4,
    EventType.STRIKE: 24,
    EventType.PLANNED_WORKS: 168,
}
DEFAULT_TTL_HOURS = 12

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    type        TEXT NOT NULL,
    severity    TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL
)
"""


class EventDB:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        with self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _expires_at(self, event: Event) -> datetime:
        ttl = TTL_HOURS.get(event.type, DEFAULT_TTL_HOURS)
        return event.started_at + timedelta(hours=ttl)

    def is_new(self, event: Event) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT expires_at FROM events WHERE id = ?", (event.id,)
            ).fetchone()
        if row is None:
            return True
        return row["expires_at"] < now

    def save(self, event: Event) -> None:
        expires_at = self._expires_at(event).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO events (id, source, type, severity, started_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.source,
                    event.type,
                    event.severity,
                    event.started_at.isoformat(),
                    expires_at,
                ),
            )

    def cleanup_expired(self) -> None:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM events WHERE expires_at < ?", (now,))

    def get_active(self, limit: int = 20) -> list[sqlite3.Row]:
        now = datetime.now(UTC).isoformat()
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM events WHERE expires_at >= ? ORDER BY started_at DESC LIMIT ?",
                (now, limit),
            ).fetchall()
