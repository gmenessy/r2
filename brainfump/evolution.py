"""Sprint 3 — Evolution Memory.

Speichert Änderungen als Patches (EvoMem-Gedanke): nicht nur "Was gilt?",
sondern "Seit wann gilt es, wodurch wurde es ersetzt, für welchen Scope?".
Alte Präferenzen werden nie blind weiterverwendet — der Validity Resolver
liefert immer den zum Stichtag gültigen Stand.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from brainfump._locking import locked
from brainfump.events import utc_now
from brainfump.memory_cards import MemoryCard, MemoryCardStore

PATCH_TYPES = frozenset({"replace", "scope_exception", "deprecate", "extend"})


@dataclass(frozen=True)
class EvolutionPatch:
    target_memory: str
    patch_type: str
    new_value: str | None = None
    old_value: str | None = None
    scope: tuple[str, ...] = ()
    valid_from: str = field(default_factory=lambda: date.today().isoformat())
    created_at: str = field(default_factory=utc_now)
    patch_id: str = field(default_factory=lambda: f"patch_{uuid.uuid4().hex[:12]}")

    def __post_init__(self) -> None:
        if self.patch_type not in PATCH_TYPES:
            raise ValueError(f"unknown patch_type: {self.patch_type!r}")
        if self.patch_type in ("replace", "scope_exception", "extend") and not self.new_value:
            raise ValueError(f"{self.patch_type} patch requires new_value")

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "target_memory": self.target_memory,
            "patch_type": self.patch_type,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "scope": list(self.scope),
            "valid_from": self.valid_from,
            "created_at": self.created_at,
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS patches (
    patch_id      TEXT PRIMARY KEY,
    target_memory TEXT NOT NULL,
    patch_type    TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    scope         TEXT NOT NULL,
    valid_from    TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_patches_target ON patches (target_memory);
"""


class EvolutionMemory:
    """Patch-basierte Versionierung über dem Memory Card Store."""

    def __init__(self, store: MemoryCardStore, path: str = ":memory:") -> None:
        self.store = store
        # Eigenes Lock für die Patch-Connection. Hält stets nur dieses Lock und
        # ruft dann store-Methoden (die ihr eigenes Lock nehmen) — nie umgekehrt,
        # daher keine Lock-Zyklen/Deadlocks.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)

    @locked
    def apply_patch(self, patch: EvolutionPatch) -> MemoryCard | None:
        target = self.store.get(patch.target_memory)
        if target is None:
            raise KeyError(patch.target_memory)
        self._persist(patch)

        if patch.patch_type == "replace":
            new_card = replace(
                target,
                card_id=f"mem_{uuid.uuid4().hex[:12]}",
                statement=patch.new_value,
                valid_from=patch.valid_from,
                valid_to=None,
                status="active",
                created_at=utc_now(),
            )
            return self.store.supersede(target.card_id, new_card)

        if patch.patch_type == "scope_exception":
            # Die Grundregel bleibt aktiv; für den Ausnahme-Scope entsteht
            # eine eigene Karte, die im Retrieval Vorrang hat.
            exception = MemoryCard(
                memory_type=target.memory_type,
                statement=patch.new_value,
                case_id=target.case_id,
                scope=patch.scope or target.scope,
                valid_from=patch.valid_from,
                confidence=target.confidence,
                trust=target.trust,
                evidence=target.evidence,
                payload={**target.payload, "exception_to": target.card_id},
            )
            return self.store.add(exception)

        if patch.patch_type == "extend":
            new_card = replace(
                target,
                card_id=f"mem_{uuid.uuid4().hex[:12]}",
                statement=f"{target.statement} {patch.new_value}",
                valid_from=patch.valid_from,
                valid_to=None,
                status="active",
                created_at=utc_now(),
            )
            return self.store.supersede(target.card_id, new_card)

        # deprecate
        self.store.set_status(target.card_id, "archived")
        self.store.set_valid_to(target.card_id, patch.valid_from)
        return None

    @locked
    def patches_for(self, target_memory: str) -> list[EvolutionPatch]:
        rows = self._conn.execute(
            "SELECT * FROM patches WHERE target_memory = ? ORDER BY valid_from",
            (target_memory,),
        )
        return [self._row_to_patch(r) for r in rows]

    def resolve(
        self,
        card_id: str,
        on_date: str | None = None,
        scope: str | None = None,
    ) -> MemoryCard | None:
        """Validity Resolver: gültiger Stand einer Memory zum Stichtag/Scope.

        Folgt der Versionskette nach vorn und bevorzugt Scope-Ausnahmen,
        die auf die aufgelöste Karte zeigen.
        """
        on_date = on_date or date.today().isoformat()
        chain = self._full_chain(card_id)
        # Auch superseded Versionen sind in ihrem historischen Fenster gültig
        # — nur archivierte (deprecated) Stände nicht.
        valid = [
            c
            for c in chain
            if c.status != "archived"
            and c.valid_from <= on_date
            and (c.valid_to is None or on_date < c.valid_to)
        ]
        resolved = valid[-1] if valid else None
        if resolved is None:
            return None
        if scope is not None:
            for exc in self.store.active(case_id=resolved.case_id, on_date=on_date):
                if exc.payload.get("exception_to") in {c.card_id for c in chain}:
                    if scope in exc.scope:
                        return exc
        return resolved

    def detect_conflicts(self, case_id: str | None = None) -> list[tuple[MemoryCard, MemoryCard]]:
        """Aktive Karten gleichen Typs mit überlappendem Scope, aber
        unterschiedlicher Aussage zum selben payload-Schlüssel."""
        cards = self.store.active(case_id=case_id)
        conflicts = []
        for i, a in enumerate(cards):
            for b in cards[i + 1 :]:
                if a.memory_type != b.memory_type:
                    continue
                if a.payload.get("exception_to") or b.payload.get("exception_to"):
                    continue
                if not (set(a.scope) & set(b.scope)):
                    continue
                shared_keys = set(a.payload) & set(b.payload)
                if any(a.payload[k] != b.payload[k] for k in shared_keys):
                    conflicts.append((a, b))
        return conflicts

    def _full_chain(self, card_id: str) -> list[MemoryCard]:
        chain = self.store.history(card_id)
        if not chain:
            return []
        # history() läuft nur rückwärts; Nachfolger über supersedes-Verweise suchen.
        last = chain[-1]
        while (successor := self.store.successor_of(last.card_id)) is not None:
            chain.append(successor)
            last = successor
        return chain

    def _persist(self, patch: EvolutionPatch) -> None:
        self._conn.execute(
            "INSERT INTO patches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                patch.patch_id,
                patch.target_memory,
                patch.patch_type,
                patch.old_value,
                patch.new_value,
                json.dumps(list(patch.scope)),
                patch.valid_from,
                patch.created_at,
            ),
        )
        self._conn.commit()

    @staticmethod
    def _row_to_patch(row: tuple) -> EvolutionPatch:
        return EvolutionPatch(
            patch_id=row[0],
            target_memory=row[1],
            patch_type=row[2],
            old_value=row[3],
            new_value=row[4],
            scope=tuple(json.loads(row[5])),
            valid_from=row[6],
            created_at=row[7],
        )
