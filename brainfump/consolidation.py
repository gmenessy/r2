"""Dream / Consolidation Layer (Abschnitt 4.7).

Offline-Verarbeitung, getrennt vom schnellen Online-Retrieval
(LightMem-Gedanke: teure Konsolidierung asynchron, schneller Abruf
synchron). Dedupliziert, erkennt Widersprüche und archiviert abgelaufene
oder schwache Karten — löscht aber nie, sondern ändert nur den Status.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from brainfump.memory_cards import MemoryCard, MemoryCardStore

_NEGATION = re.compile(r"\b(kein|keine|keinen|nicht|no|not|never|niemals)\b", re.IGNORECASE)
_TOKEN = re.compile(r"[a-zäöüß0-9]+", re.IGNORECASE)


def _normalize(statement: str) -> str:
    return " ".join(_TOKEN.findall(statement.lower()))


@dataclass
class ConsolidationReport:
    deduplicated: list[tuple[str, str]] = field(default_factory=list)  # (kept, merged)
    contradictions: list[tuple[str, str]] = field(default_factory=list)
    archived: list[str] = field(default_factory=list)


class Consolidator:
    def __init__(self, store: MemoryCardStore, graph=None) -> None:
        self.store = store
        # Optionaler Memory Graph: erkannte Widersprüche werden als
        # contradicts-Kanten (mit winner/loser) festgehalten.
        self.graph = graph

    def consolidate(
        self,
        case_id: str | None = None,
        min_confidence: float = 0.1,
        today: str | None = None,
    ) -> ConsolidationReport:
        report = ConsolidationReport()
        self._deduplicate(case_id, report)
        self._detect_contradictions(case_id, report)
        self._archive_stale(case_id, min_confidence, today or date.today().isoformat(), report)
        return report

    def _deduplicate(self, case_id: str | None, report: ConsolidationReport) -> None:
        seen: dict[tuple, MemoryCard] = {}
        for card in self.store.active(case_id=case_id, include_global=case_id is None):
            key = (card.case_id, card.memory_type, _normalize(card.statement))
            kept = seen.get(key)
            if kept is None:
                seen[key] = card
                continue
            # Duplikat: Evidenz auf der behaltenen Karte zusammenführen,
            # die schwächere Karte als superseded markieren.
            merged = MemoryCard(
                memory_type=kept.memory_type,
                statement=kept.statement,
                case_id=kept.case_id,
                scope=tuple(dict.fromkeys(kept.scope + card.scope)),
                valid_from=min(kept.valid_from, card.valid_from),
                confidence=max(kept.confidence, card.confidence),
                trust=max(kept.trust, card.trust),
                evidence=tuple(dict.fromkeys(kept.evidence + card.evidence)),
                payload={**card.payload, **kept.payload},
            )
            new = self.store.supersede(kept.card_id, merged)
            self.store.set_status(card.card_id, "superseded")
            report.deduplicated.append((new.card_id, card.card_id))
            seen[key] = new

    def _detect_contradictions(self, case_id: str | None, report: ConsolidationReport) -> None:
        """Zwei aktive Karten widersprechen sich, wenn sie im selben Scope
        dieselbe Kernaussage einmal verneint und einmal bejaht treffen.

        Vergleiche laufen nur innerhalb von (case_id, memory_type)-Buckets —
        das schneidet die O(n²)-Paarmenge auf die tatsächlich vergleichbaren
        Karten zu (Karten verschiedener Akten/Typen können nie widersprechen)."""
        cards = self.store.active(case_id=case_id, include_global=case_id is None)
        buckets: dict[tuple, list] = {}
        for card in cards:
            buckets.setdefault((card.case_id, card.memory_type), []).append(card)

        for bucket in buckets.values():
            for i, a in enumerate(bucket):
                for b in bucket[i + 1 :]:
                    if a.scope and b.scope and not (set(a.scope) & set(b.scope)):
                        continue
                    if not self._contradicts(a.statement, b.statement):
                        continue
                    report.contradictions.append((a.card_id, b.card_id))
                    # Trust-gewichtet: die vertrauenswürdigere Aussage überlebt.
                    # Nur bei Gleichstand werden beide stillgelegt — so kann
                    # eine untrusted Quelle wahres Wissen nicht per Widerspruch
                    # löschen (Truth-Suppression).
                    if a.trust > b.trust:
                        loser, winner = b.card_id, a.card_id
                        self.store.set_status(b.card_id, "contradicted")
                    elif b.trust > a.trust:
                        loser, winner = a.card_id, b.card_id
                        self.store.set_status(a.card_id, "contradicted")
                    else:
                        loser, winner = None, None
                        self.store.set_status(a.card_id, "contradicted")
                        self.store.set_status(b.card_id, "contradicted")
                    if self.graph is not None:
                        self.graph.add_edge(
                            a.card_id, b.card_id, "contradicts",
                            meta={"winner": winner, "loser": loser},
                        )

    @staticmethod
    def _contradicts(stmt_a: str, stmt_b: str) -> bool:
        neg_a = bool(_NEGATION.search(stmt_a))
        neg_b = bool(_NEGATION.search(stmt_b))
        if neg_a == neg_b:
            return False
        tokens_a = set(_TOKEN.findall(_NEGATION.sub(" ", stmt_a.lower())))
        tokens_b = set(_TOKEN.findall(_NEGATION.sub(" ", stmt_b.lower())))
        union = tokens_a | tokens_b
        if not union:
            return False
        # Konservative Schwelle: contradicted legt beide Karten still, ein
        # Falsch-Positiv ist also teuer. Die Negation wird global pro Statement
        # erkannt — bei Mehrsatz-Statements mit nur teilweiser Überlappung
        # (z. B. gemeinsames Thema, verschiedene Aussagen) wäre 0.5 zu lax.
        return len(tokens_a & tokens_b) / len(union) >= 0.8

    def _archive_stale(
        self,
        case_id: str | None,
        min_confidence: float,
        today: str,
        report: ConsolidationReport,
    ) -> None:
        for card in self.store.active(case_id=case_id, include_global=case_id is None):
            expired = card.valid_to is not None and card.valid_to <= today
            weak = card.confidence < min_confidence
            if expired or weak:
                self.store.set_status(card.card_id, "archived")
                report.archived.append(card.card_id)
