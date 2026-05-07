import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from models.db import EventDB
from models.event import Event, EventType, Severity


def make_test_event(event_id: str, event_type: EventType = EventType.FIRE) -> Event:
    return Event(
        id=event_id,
        source="test",
        type=event_type,
        title="Test Event",
        description="Test description",
        lat=38.7169,
        lon=-9.1399,
        severity=Severity.LOW,
        status="active",
        started_at=datetime.now(timezone.utc),
    )


@pytest.fixture
def db():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    event_db = EventDB(db_path)
    yield event_db
    try:
        os.unlink(db_path)
    except PermissionError:
        pass  # Windows may hold a lock; file will be cleaned up on next run


def test_new_event_is_new(db):
    event = make_test_event("evt-001")
    assert db.is_new(event) is True


def test_saved_event_still_new_until_notified(db):
    event = make_test_event("evt-002")
    db.save(event)
    # save() alone does not suppress re-notification; mark_notified() does
    assert db.is_new(event) is True
    db.mark_notified(event.id)
    assert db.is_new(event) is False


def test_notified_event_not_new_even_after_expiry(db):
    event = make_test_event("evt-003", EventType.FIRE)
    db.save(event)
    db.mark_notified(event.id)

    # Expiry does not reset the notified flag
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn = sqlite3.connect(db.db_path)
    conn.execute("UPDATE events SET expires_at = ? WHERE id = ?", (past, event.id))
    conn.commit()
    conn.close()

    assert db.is_new(event) is False


def test_different_ids_are_independent(db):
    event_a = make_test_event("evt-004")
    event_b = make_test_event("evt-005")

    db.save(event_a)
    db.mark_notified(event_a.id)

    assert db.is_new(event_a) is False
    assert db.is_new(event_b) is True


def test_cleanup_removes_expired_keeps_active(db):
    active_event = make_test_event("evt-active")
    expired_event = make_test_event("evt-expired")

    db.save(active_event)
    db.save(expired_event)

    # Force the expired event's expires_at into the past
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    conn = sqlite3.connect(db.db_path)
    conn.execute(
        "UPDATE events SET expires_at = ? WHERE id = ?", (past, expired_event.id)
    )
    conn.commit()
    conn.close()

    db.cleanup_expired()

    active_rows = db.get_active()
    ids = [row["id"] for row in active_rows]

    assert "evt-active" in ids
    assert "evt-expired" not in ids
