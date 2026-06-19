"""Sprint 1 — Memory Card Store.

Strukturierte, typisierte Erinnerungen statt Rohchunks (Prinzip 3.2).
Karten sind case-bounded (Prinzip 3.3): jede Akte besitzt einen eigenen
Memory-Raum; case_id=None markiert globale DNA-/Governance-Karten.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from brainfump.events import utc_now

MEMORY_TYPES = frozenset(
    {
        "episodic",
        "semantic",
        "procedural",
        "preference",
        "governance",
        "evolution",
        "failure",
        "skill",
        "risk",
    }
)

STATUSES = frozenset({"active", "superseded", "archived", "contradicted"})


@dataclass(frozen=True)
class MemoryCard:
    memory_type: str
    statement: str
    case_id: str | None = None
    scope: tuple[str, ...] = ()
    status: str = "active"
    valid_from: str = field(default_factory=lambda: date.today().isoformat())
    valid_to: str | None = None
    confidence: float = 1.0
    trust: float = 1.0
    evidence: tuple[str, ...] = ()
    payload: dict[str, Any] = field(default_factory=dict)
    supersedes: str | None = None
    created_at: str = field(default_factory=utc_now)
    card_id: str = field(default_factory=lambda: f"mem_{uuid.uuid4().hex[:12]}")

    def __post_init__(self) -> None:
        if self.memory_type not in MEMORY_TYPES:
            raise ValueError(f"unknown memory_type: {self.memory_type!r}")
        if self.status not in STATUSES:
            raise ValueError(f"unknown status: {self.status!r}")

    def is_valid_on(self, day: str) -> bool:
        if self.status != "active":
            return False
        if day < self.valid_from:
            return False
        return self.valid_to is None or day < self.valid_to

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "memory_type": self.memory_type,
            "statement": self.statement,
            "case_id": self.case_id,
            "scope": list(self.scope),
            "status": self.status,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
            "confidence": self.confidence,
            "trust": self.trust,
            "evidence": list(self.evidence),
            "payload": self.payload,
            "supersedes": self.supersedes,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryCard":
        """Inverse zu :meth:`to_dict`; tolerant gegenüber JSON-Listen für
        die als Tupel gehaltenen Felder ``scope`` und ``evidence``."""
        data = dict(data)
        data["scope"] = tuple(data.get("scope", ()))
        data["evidence"] = tuple(data.get("evidence", ()))
        return cls(**data)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    card_id     TEXT PRIMARY KEY,
    memory_type TEXT NOT NULL,
    statement   TEXT NOT NULL,
    case_id     TEXT,
    scope       TEXT NOT NULL,
    status      TEXT NOT NULL,
    valid_from  TEXT NOT NULL,
    valid_to    TEXT,
    confidence  REAL NOT NULL,
    trust       REAL NOT NULL,
    evidence    TEXT NOT NULL,
    payload     TEXT NOT NULL,
    supersedes  TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cards_case ON cards (case_id);
CREATE INDEX IF NOT EXISTS idx_cards_type ON cards (memory_type);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards (status);
"""


class MemoryCardStore:
    """Memory Card Store (vfs://memory/cards/)."""

    def __init__(self, path: str = ":memory:") -> None:
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)

    def add(self, card: MemoryCard) -> MemoryCard:
        self._conn.execute(
            "INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                card.card_id,
                card.memory_type,
                card.statement,
                card.case_id,
                json.dumps(list(card.scope)),
                card.status,
                card.valid_from,
                card.valid_to,
                card.confidence,
                card.trust,
                json.dumps(list(card.evidence)),
                json.dumps(card.payload),
                card.supersedes,
                card.created_at,
            ),
        )
        self._conn.commit()
        return card

    def get(self, card_id: str) -> MemoryCard | None:
        row = self._conn.execute(
            "SELECT * FROM cards WHERE card_id = ?", (card_id,)
        ).fetchone()
        return self._row_to_card(row) if row else None

    def active(
        self,
        case_id: str | None = None,
        memory_type: str | None = None,
        scope: str | None = None,
        include_global: bool = True,
        on_date: str | None = None,
    ) -> list[MemoryCard]:
        """Case-bounded Abruf: Karten der Akte, optional ergänzt um globale DNA.

        Reihenfolge gemäß Prinzip 3.3: zuerst Case Memory, danach Global DNA.
        """
        sql = "SELECT * FROM cards WHERE status = 'active'"
        params: list[Any] = []
        if case_id is not None and include_global:
            sql += " AND (case_id = ? OR case_id IS NULL)"
            params.append(case_id)
        elif case_id is not None:
            sql += " AND case_id = ?"
            params.append(case_id)
        elif not include_global:
            sql += " AND case_id IS NOT NULL"
        if memory_type is not None:
            sql += " AND memory_type = ?"
            params.append(memory_type)
        sql += " ORDER BY (case_id IS NULL), created_at"
        cards = [self._row_to_card(r) for r in self._conn.execute(sql, params)]
        if scope is not None:
            cards = [c for c in cards if scope in c.scope or not c.scope]
        if on_date is not None:
            cards = [c for c in cards if c.is_valid_on(on_date)]
        return cards

    def set_status(self, card_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status!r}")
        self._conn.execute(
            "UPDATE cards SET status = ? WHERE card_id = ?", (status, card_id)
        )
        self._conn.commit()

    def set_valid_to(self, card_id: str, valid_to: str) -> None:
        self._conn.execute(
            "UPDATE cards SET valid_to = ? WHERE card_id = ?", (valid_to, card_id)
        )
        self._conn.commit()

    def supersede(self, old_card_id: str, new_card: MemoryCard) -> MemoryCard:
        """Versionierung: Alte Karte wird ersetzt, nie gelöscht."""
        old = self.get(old_card_id)
        if old is None:
            raise KeyError(old_card_id)
        self.set_status(old_card_id, "superseded")
        self.set_valid_to(old_card_id, new_card.valid_from)
        return self.add(replace(new_card, supersedes=old_card_id))

    def history(self, card_id: str) -> list[MemoryCard]:
        """Versionskette einer Karte, älteste zuerst."""
        chain: list[MemoryCard] = []
        current = self.get(card_id)
        while current is not None:
            chain.append(current)
            current = self.get(current.supersedes) if current.supersedes else None
        return list(reversed(chain))

    def successor_of(self, card_id: str) -> MemoryCard | None:
        """Die Karte, die ``card_id`` direkt ersetzt hat (oder None)."""
        row = self._conn.execute(
            "SELECT card_id FROM cards WHERE supersedes = ?", (card_id,)
        ).fetchone()
        return self.get(row[0]) if row else None

    def __len__(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]

    @staticmethod
    def _row_to_card(row: tuple) -> MemoryCard:
        return MemoryCard(
            card_id=row[0],
            memory_type=row[1],
            statement=row[2],
            case_id=row[3],
            scope=tuple(json.loads(row[4])),
            status=row[5],
            valid_from=row[6],
            valid_to=row[7],
            confidence=row[8],
            trust=row[9],
            evidence=tuple(json.loads(row[10])),
            payload=json.loads(row[11]),
            supersedes=row[12],
            created_at=row[13],
        )
