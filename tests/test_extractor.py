"""Sprint 1: Event → Memory Card Extraktion (deterministisch)."""

from brainfump.events import EventLog
from brainfump.extractor import MemoryExtractor
from brainfump.memory_cards import MemoryCardStore


def make_kernel_parts():
    log = EventLog()
    store = MemoryCardStore()
    return log, store, MemoryExtractor(store)


def test_correction_becomes_preference_card():
    log, store, extractor = make_kernel_parts()
    event = log.record(
        "correction",
        "Bitte keine React-Abhängigkeit im MVP.",
        case_id="akte_1",
        confidence=0.95,
        payload={"scope": ["frontend", "mvp"]},
    )
    card = extractor.extract(event)
    assert card.memory_type == "preference"
    assert card.case_id == "akte_1"
    assert card.scope == ("frontend", "mvp")
    assert card.evidence == (event.event_id,)
    assert card.confidence == 0.95


def test_event_type_mapping():
    log, store, extractor = make_kernel_parts()
    cases = {
        "decision": "semantic",
        "failed_attempt": "failure",
        "successful_attempt": "skill",
        "policy_violation": "governance",
        "risk_marker": "risk",
        "document_change": "evolution",
    }
    for event_type, memory_type in cases.items():
        card = extractor.extract(log.record(event_type, f"Inhalt {event_type}"))
        assert card.memory_type == memory_type


def test_routine_events_produce_no_cards():
    log, store, extractor = make_kernel_parts()
    assert extractor.extract(log.record("tool_call", "ls -la")) is None
    assert extractor.extract(log.record("user_input", "hi")) is None


def test_memorable_episodic_event_produces_card():
    log, store, extractor = make_kernel_parts()
    event = log.record(
        "user_input",
        "Der Mandant hat die Frist auf den 1. Juli verschoben.",
        payload={"memorable": True},
    )
    card = extractor.extract(event)
    assert card.memory_type == "episodic"


def test_string_scope_is_normalized():
    """Hygiene: payload scope="frontend" (String) wird zu ("frontend",),
    nicht zum Zeichen-Tupel ('f','r',…)."""
    log, store, extractor = make_kernel_parts()
    event = log.record("decision", "X", payload={"scope": "frontend"})
    card = extractor.extract(event)
    assert card.scope == ("frontend",)


def test_extract_all_persists_cards():
    log, store, extractor = make_kernel_parts()
    log.record("decision", "A")
    log.record("risk_marker", "B", payload={"fragile_file": "core.py"})
    log.record("tool_call", "C")
    cards = extractor.extract_all(log)
    assert len(cards) == 2
    assert len(store) == 2
    risk = [c for c in cards if c.memory_type == "risk"][0]
    assert risk.payload == {"fragile_file": "core.py"}
