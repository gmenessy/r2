"""Sprint 1: Event Log — append-only, beweissicher."""

import sqlite3

import pytest

from brainfump.events import Event, EventLog


def test_append_and_get():
    log = EventLog()
    event = log.record(
        "decision",
        "Frontend bleibt Vanilla JS.",
        case_id="agentische_akte",
        source="chat",
        confidence=0.92,
    )
    loaded = log.get(event.event_id)
    assert loaded == event
    assert len(log) == 1


def test_unknown_event_type_rejected():
    with pytest.raises(ValueError):
        Event(event_type="brainstorm", content="x")


def test_confidence_bounds():
    with pytest.raises(ValueError):
        Event(event_type="decision", content="x", confidence=1.5)


def test_query_by_case_and_type():
    log = EventLog()
    log.record("decision", "A", case_id="akte_1")
    log.record("correction", "B", case_id="akte_1")
    log.record("decision", "C", case_id="akte_2")

    assert len(log.query(case_id="akte_1")) == 2
    assert len(log.query(event_type="decision")) == 2
    assert [e.content for e in log.query(case_id="akte_1", event_type="decision")] == ["A"]


def test_log_is_append_only():
    log = EventLog()
    event = log.record("decision", "unveränderlich")
    with pytest.raises(sqlite3.IntegrityError):
        log._conn.execute(
            "UPDATE events SET content = 'manipuliert' WHERE event_id = ?",
            (event.event_id,),
        )
    with pytest.raises(sqlite3.IntegrityError):
        log._conn.execute("DELETE FROM events WHERE event_id = ?", (event.event_id,))


def test_roundtrip_serialization():
    event = Event(event_type="risk_marker", content="fragile Datei", payload={"file": "a.py"})
    assert Event.from_dict(event.to_dict()) == event
