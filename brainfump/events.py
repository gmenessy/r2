"""Sprint 1 — Event Log.

Append-only Speicher für alle relevanten Agentenereignisse (Prinzip 3.1,
"Event First"): zuerst beweissicher erfassen, nie intelligent überschreiben.
SQLite-Trigger verhindern UPDATE und DELETE auf Datenbankebene.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator

from brainfump._locking import locked

EVENT_TYPES = frozenset(
    {
        "user_input",
        "agent_action",
        "tool_call",
        "decision",
        "correction",
        "failed_attempt",
        "successful_attempt",
        "document_change",
        "policy_violation",
        "risk_marker",
    }
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Event:
    event_type: str
    content: str
    case_id: str | None = None
    source: str = "agent"
    confidence: float = 1.0
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)
    event_id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:12]}")

    def __post_init__(self) -> None:
        if self.event_type not in EVENT_TYPES:
            raise ValueError(f"unknown event_type: {self.event_type!r}")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within [0, 1]")

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "case_id": self.case_id,
            "content": self.content,
            "source": self.source,
            "confidence": self.confidence,
            "payload": self.payload,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Event":
        return cls(**data)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id   TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    case_id    TEXT,
    content    TEXT NOT NULL,
    source     TEXT NOT NULL,
    confidence REAL NOT NULL,
    payload    TEXT NOT NULL,
    timestamp  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_case ON events (case_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);
CREATE TRIGGER IF NOT EXISTS events_no_update
    BEFORE UPDATE ON events
    BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete
    BEFORE DELETE ON events
    BEGIN SELECT RAISE(ABORT, 'event log is append-only'); END;
"""


class EventLog:
    """Append-only Event Log (vfs://events/)."""

    def __init__(self, path: str = ":memory:") -> None:
        # check_same_thread=False: der HTTP-Server bedient Requests in
        # Worker-Threads. Der Zugriff wird über self._lock (@locked) serialisiert.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)

    @locked
    def append(self, event: Event) -> Event:
        self._conn.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.event_id,
                event.event_type,
                event.case_id,
                event.content,
                event.source,
                event.confidence,
                json.dumps(event.payload),
                event.timestamp,
            ),
        )
        self._conn.commit()
        return event

    def record(self, event_type: str, content: str, **kwargs: Any) -> Event:
        return self.append(Event(event_type=event_type, content=content, **kwargs))

    @locked
    def get(self, event_id: str) -> Event | None:
        row = self._conn.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._row_to_event(row) if row else None

    @locked
    def query(
        self,
        case_id: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
    ) -> list[Event]:
        sql = "SELECT * FROM events WHERE 1=1"
        params: list[Any] = []
        if case_id is not None:
            sql += " AND case_id = ?"
            params.append(case_id)
        if event_type is not None:
            sql += " AND event_type = ?"
            params.append(event_type)
        if since is not None:
            sql += " AND timestamp >= ?"
            params.append(since)
        sql += " ORDER BY timestamp"
        return [self._row_to_event(r) for r in self._conn.execute(sql, params)]

    def __iter__(self) -> Iterator[Event]:
        return iter(self.query())

    @locked
    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    @staticmethod
    def _row_to_event(row: tuple) -> Event:
        return Event(
            event_id=row[0],
            event_type=row[1],
            case_id=row[2],
            content=row[3],
            source=row[4],
            confidence=row[5],
            payload=json.loads(row[6]),
            timestamp=row[7],
        )
