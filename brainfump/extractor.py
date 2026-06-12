"""Sprint 1 — Memory Extractor.

Deterministische Übersetzung Event → Memory Card. Keine LLM-Abhängigkeit:
Die Typisierung folgt festen Regeln, damit die Extraktion auditierbar und
reproduzierbar bleibt (PROJECTMEM-Gedanke: deterministisch verdichten).
"""

from __future__ import annotations

from brainfump.events import Event, EventLog
from brainfump.memory_cards import MemoryCard, MemoryCardStore

# Welcher Event-Typ erzeugt welchen Memory-Typ.
_EVENT_TO_MEMORY = {
    "correction": "preference",
    "decision": "semantic",
    "failed_attempt": "failure",
    "successful_attempt": "skill",
    "policy_violation": "governance",
    "risk_marker": "risk",
    "document_change": "evolution",
}

# Episodische Karten nur, wenn das Event als merkwürdig markiert wurde —
# sonst flutet jeder Tool-Call den Card Store.
_EPISODIC_TYPES = frozenset({"user_input", "agent_action", "tool_call"})


class MemoryExtractor:
    """Erzeugt Memory Cards aus Events und legt sie im Store ab."""

    def __init__(self, store: MemoryCardStore) -> None:
        self.store = store

    def extract(self, event: Event) -> MemoryCard | None:
        memory_type = _EVENT_TO_MEMORY.get(event.event_type)
        if memory_type is None:
            if event.event_type in _EPISODIC_TYPES and event.payload.get("memorable"):
                memory_type = "episodic"
            else:
                return None

        scope = tuple(event.payload.get("scope", ()))
        payload = {
            k: v for k, v in event.payload.items() if k not in ("scope", "memorable")
        }
        card = MemoryCard(
            memory_type=memory_type,
            statement=event.content,
            case_id=event.case_id,
            scope=scope,
            confidence=event.confidence,
            evidence=(event.event_id,),
            payload=payload,
            valid_from=event.timestamp[:10],
        )
        return self.store.add(card)

    def extract_all(self, log: EventLog) -> list[MemoryCard]:
        cards = []
        for event in log:
            card = self.extract(event)
            if card is not None:
                cards.append(card)
        return cards
