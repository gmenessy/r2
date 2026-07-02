"""Coding-Agent-Guard (App Nr. 2) — PROJECTMEM für Coding Agents.

Ein Wächter-Service, dem Coding Agents Entwicklungsereignisse melden
(Entscheidungen, gescheiterte Fixes, fragile Dateien, Korrekturen) und
den sie vor jedem Edit befragen. Drei Funktionen:

1. Event-Aufnahme  → Event Log + Memory Cards (+ Regeln aus Korrekturen)
2. Pre-Edit Gate   → block / warn / require_review / suggest_alternative
3. Kontext-Summary → deterministische, kompakte Projektzusammenfassung
                     zur Injektion in den Agenten-Kontext

Ein Repository/Projekt entspricht einer Akte (case_id).
"""

from __future__ import annotations

from typing import Any

from brainfump import BrainFumpKernel

# Welche gemeldeten Ereignisse akzeptiert werden (Teilmenge der Event-Typen,
# ergänzt um den Shortcut "fragile_file").
_REPORTABLE = frozenset(
    {
        "decision",
        "correction",
        "failed_attempt",
        "successful_attempt",
        "risk_marker",
        "document_change",
        "policy_violation",
    }
)


class CodingAgentGuard:
    def __init__(self, kernel: BrainFumpKernel) -> None:
        self.kernel = kernel

    # -- Event-Aufnahme ----------------------------------------------------------

    def report(self, repo: str, report_type: str, content: str, **payload: Any) -> dict[str, Any]:
        """Entwicklungsereignis aufnehmen.

        report_type "fragile_file" ist ein Shortcut für einen risk_marker
        mit payload {"fragile_files": [...]}.
        """
        if report_type == "fragile_file":
            files = payload.pop("files", None)
            if not files:
                single = payload.pop("file", None)
                if not single:
                    raise ValueError(
                        "report_type 'fragile_file' requires payload 'files' (list) or 'file'"
                    )
                files = [single]
            event = self.kernel.record(
                "risk_marker",
                content or f"Fragile Datei(en): {files}",
                case_id=repo,
                source="coding_agent",
                payload={**payload, "fragile_files": list(files)},
            )
        elif report_type in _REPORTABLE:
            event = self.kernel.record(
                report_type,
                content,
                case_id=repo,
                source="coding_agent",
                payload=payload,
            )
        else:
            raise ValueError(f"unknown report_type: {report_type!r}")
        return {"event_id": event.event_id}

    # -- Pre-Edit Gate -------------------------------------------------------------

    def gate(
        self,
        repo: str,
        action_type: str = "edit",
        files: list[str] | None = None,
        error_signature: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        decision = self.kernel.check_action(
            {
                "action_type": action_type,
                "case_id": repo,
                "files": files or [],
                "error_signature": error_signature,
                "context": context or {},
            }
        )
        return decision.to_dict()

    # -- Kontext-Summary -------------------------------------------------------------

    def summary(self, repo: str, max_items: int = 10) -> str:
        """Deterministische Markdown-Zusammenfassung des Projektgedächtnisses
        — gedacht zur Injektion in den Kontext eines Coding Agents."""
        cards = self.kernel.cards.active(case_id=repo)
        sections: list[str] = [f"# Projektgedächtnis: {repo}"]

        def add_section(title: str, memory_type: str, fmt=lambda c: c.statement) -> None:
            matching = [c for c in cards if c.memory_type == memory_type][-max_items:]
            if matching:
                sections.append(f"\n## {title}")
                sections.extend(f"- {fmt(c)}" for c in matching)

        add_section("Architekturentscheidungen", "semantic")
        add_section(
            "Fragile Dateien (Änderung nur mit Review)",
            "risk",
            lambda c: ", ".join(c.payload.get("fragile_files", [])) + f" — {c.statement}",
        )
        add_section(
            "Bereits gescheiterte Fixes (nicht wiederholen)",
            "failure",
            lambda c: c.statement
            + (
                f" → Alternative: {c.payload['alternative']}"
                if c.payload.get("alternative")
                else ""
            ),
        )
        add_section("Präferenzen/Korrekturen", "preference")
        add_section("Bewährte Vorgehensweisen", "skill")

        rules = [r for r in self.kernel.checker.rules if r.condition.get("case_id") in (repo, None)]
        if rules:
            sections.append("\n## Aktive Regeln (werden vor Task-Abschluss geprüft)")
            sections.extend(f"- `{r.rule_id}`: {r.message}" for r in rules[-max_items:])

        if len(sections) == 1:
            sections.append("\nNoch keine Erinnerungen zu diesem Projekt.")
        return "\n".join(sections)

    def stats(self, repo: str) -> dict[str, Any]:
        events = self.kernel.events.query(case_id=repo)
        gates = [
            e for e in events if e.event_type == "agent_action" and e.source == "gatekeeper"
        ]
        return {
            "events": len(events),
            "memory_cards": len(self.kernel.cards.active(case_id=repo)),
            "gate_checks": len(gates),
            "interventions": sum(
                1 for e in gates if e.payload["decision"]["mode"] != "allow"
            ),
            "active_rules": len(self.kernel.checker.rules),
        }
