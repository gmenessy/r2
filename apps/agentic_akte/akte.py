"""Agentische Akte (App Nr. 1) — aktenbasierter Agent mit Gedächtnis-Gate.

Jede Akte ist ein eigener Memory-Raum (case_id). Die Akte merkt sich
Dokumentstände, Entscheidungen, Fristen, offene Punkte und Risiken —
und das Event Log ist zugleich die revisionssichere Aktenhistorie.
Vor kritischen Aktionen (Dokument ändern, Entwurf versenden, löschen)
läuft der Memory Gatekeeper; das Ergebnis erscheint in der UI als
Warnkarte (Sprint-2-Deliverable der Spec).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from brainfump import BrainFumpKernel
from brainfump.memory_cards import MemoryCard


class AgenticAkte:
    def __init__(self, kernel: BrainFumpKernel) -> None:
        self.kernel = kernel
        self._ensure_global_dna()

    def _ensure_global_dna(self) -> None:
        """Organisationsweite Governance, die in jeder Akte gilt."""
        existing = self.kernel.cards.active(case_id=None, memory_type="governance")
        if any(c.payload.get("dna_rule") == "no_auto_delete" for c in existing):
            return
        self.kernel.cards.add(
            MemoryCard(
                memory_type="governance",
                statement="Aktendokumente werden nie automatisch gelöscht.",
                case_id=None,
                payload={
                    "dna_rule": "no_auto_delete",
                    "forbidden_actions": ["delete_document"],
                },
            )
        )

    # -- Akteninhalt -------------------------------------------------------------

    def add_document(
        self, case: str, name: str, summary: str, fragile: bool = False, reason: str = ""
    ) -> dict[str, Any]:
        event = self.kernel.record(
            "document_change",
            f"Dokument '{name}' hinzugefügt/aktualisiert: {summary}",
            case_id=case,
            source="akte",
            payload={"document": name, "scope": ["dokumente"]},
        )
        result: dict[str, Any] = {"event_id": event.event_id}
        if fragile:
            risk = self.kernel.record(
                "risk_marker",
                f"'{name}' ist geschützt: {reason or 'Änderung nur nach Review.'}",
                case_id=case,
                source="akte",
                payload={"fragile_files": [name], "scope": ["dokumente"]},
            )
            result["risk_event_id"] = risk.event_id
        return result

    def record_decision(self, case: str, text: str, scope: list[str] | None = None) -> dict[str, Any]:
        event = self.kernel.record(
            "decision",
            text,
            case_id=case,
            source="user",
            payload={"scope": scope or []},
        )
        return {"event_id": event.event_id}

    def add_deadline(self, case: str, description: str, due_date: str) -> dict[str, Any]:
        event = self.kernel.record(
            "decision",
            f"Frist: {description} (fällig {due_date})",
            case_id=case,
            source="user",
            payload={"kind": "deadline", "due_date": due_date, "scope": ["fristen"]},
        )
        return {"event_id": event.event_id}

    def add_open_point(self, case: str, text: str) -> dict[str, Any]:
        event = self.kernel.record(
            "user_input",
            f"Offener Punkt: {text}",
            case_id=case,
            source="user",
            payload={"memorable": True, "kind": "open_point"},
        )
        return {"event_id": event.event_id}

    def record_failed_analysis(
        self, case: str, text: str, signature: str, alternative: str = ""
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"error_signature": signature}
        if alternative:
            payload["alternative"] = alternative
        event = self.kernel.record(
            "failed_attempt", text, case_id=case, source="akte", payload=payload
        )
        return {"event_id": event.event_id}

    # -- Abfragen -------------------------------------------------------------------

    def cases(self) -> list[str]:
        return sorted({e.case_id for e in self.kernel.events.query() if e.case_id})

    def deadlines(self, case: str, today: str | None = None) -> list[dict[str, Any]]:
        today = today or date.today().isoformat()
        result = []
        for card in self.kernel.cards.active(case_id=case, include_global=False):
            if card.payload.get("kind") != "deadline":
                continue
            due = card.payload["due_date"]
            result.append(
                {
                    "card_id": card.card_id,
                    "statement": card.statement,
                    "due_date": due,
                    "status": "overdue" if due < today else "open",
                }
            )
        return sorted(result, key=lambda d: d["due_date"])

    def timeline(self, case: str) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.kernel.events.query(case_id=case)]

    def memory(self, case: str) -> list[dict[str, Any]]:
        return [c.to_dict() for c in self.kernel.cards.active(case_id=case)]

    def wiki(self, case: str) -> str:
        """Menschenlesbare Markdown-Projektion der Akte (E-3)."""
        return self.kernel.wiki_page(case)

    # -- Gatekeeper ---------------------------------------------------------------------

    def check_action(
        self,
        case: str,
        action_type: str,
        documents: list[str] | None = None,
        error_signature: str | None = None,
    ) -> dict[str, Any]:
        decision = self.kernel.check_action(
            {
                "action_type": action_type,
                "case_id": case,
                "files": documents or [],
                "error_signature": error_signature,
            }
        )
        overdue = [d for d in self.deadlines(case) if d["status"] == "overdue"]
        result = decision.to_dict()
        result["overdue_deadlines"] = overdue
        return result

    def consolidate(self, case: str) -> dict[str, Any]:
        report = self.kernel.consolidate(case_id=case)
        return {
            "deduplicated": report.deduplicated,
            "contradictions": report.contradictions,
            "archived": report.archived,
        }
