"""Memory House (App Nr. 4) — Das Haus, das sich erinnert. 🏠

Jeder Raum ist eine Akte (case_id), Bewohner-Regeln sind globale DNA.
Bevor irgendein Automatismus feuert, fragt das Haus den Memory
Gatekeeper: Welche Erinnerungen, Regeln und vergangenen Fehler müssen
diese Aktion beeinflussen?

- failed_attempt:  „Heizung auf 24° um 6 Uhr → Bad trotzdem kalt"
                   → nie wieder so, stattdessen die gelernte Alternative
- risk_marker:     „Sicherung im Keller ist fragil" → require_review
- Haus-Regel:      „Nie staubsaugen, wenn jemand im Call ist"
                   → Runtime-Check gegen den Live-Kontext, nicht nur Notiz
- Evolution:       Sommerbetrieb ersetzt Winterbetrieb per Patch
"""

from __future__ import annotations

from typing import Any

from brainfump import BrainFumpKernel
from brainfump.evolution import EvolutionPatch
from brainfump.rules import Rule

DEFAULT_ROOMS = ("wohnzimmer", "kueche", "bad", "schlafzimmer", "buero", "keller")


class MemoryHouse:
    def __init__(self, kernel: BrainFumpKernel, rooms: tuple[str, ...] = DEFAULT_ROOMS) -> None:
        self.kernel = kernel
        self._rooms = list(rooms)
        # Kontextabhängige Hausregeln nach Neustart rekompilieren.
        for event in self.kernel.events.query(event_type="correction"):
            if "house_rule" in event.payload:
                self._compile_house_rule(event)

    # -- Handeln (mit Hausverstand) -----------------------------------------------

    def attempt(
        self,
        room: str,
        action_type: str,
        device: str | None = None,
        signature: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Aktion versuchen: erst Gatekeeper, nur bei allow/warn ausführen."""
        decision = self.kernel.check_action(
            {
                "action_type": action_type,
                "case_id": room,
                "files": [device] if device else [],
                "error_signature": signature,
                "context": context or {},
            }
        )
        executed = decision.mode.label in ("allow", "warn")
        if executed:
            self.kernel.record(
                "agent_action",
                f"{action_type}"
                + (f" ({device})" if device else "")
                + f" in {room} ausgeführt.",
                case_id=room,
                source="haus",
            )
        result = decision.to_dict()
        result["executed"] = executed
        return result

    # -- Lernen --------------------------------------------------------------------

    def remember_failure(
        self, room: str, content: str, signature: str, alternative: str = ""
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"error_signature": signature}
        if alternative:
            payload["alternative"] = alternative
        event = self.kernel.record(
            "failed_attempt", content, case_id=room, source="haus", payload=payload
        )
        return {"event_id": event.event_id}

    def mark_fragile(self, room: str, device: str, reason: str) -> dict[str, Any]:
        event = self.kernel.record(
            "risk_marker",
            f"'{device}' in {room} ist fragil: {reason}",
            case_id=room,
            source="haus",
            payload={"fragile_files": [device]},
        )
        return {"event_id": event.event_id}

    def add_house_rule(
        self,
        text: str,
        forbidden_action: str,
        when: dict[str, Any] | None = None,
        severity: str = "high",
    ) -> dict[str, Any]:
        """Bewohner-Korrektur → ausführbare Hausregel.

        Ohne `when` gilt sie absolut (globale Governance-Karte); mit `when`
        wird sie zum kontextabhängigen Runtime-Check (z. B. nur während
        eines Calls).
        """
        if when:
            event = self.kernel.record(
                "correction",
                text,
                case_id=None,
                source="bewohner",
                payload={
                    "house_rule": {
                        "forbidden_action": forbidden_action,
                        "when": dict(when),
                        "severity": severity,
                    }
                },
            )
            rule = self._compile_house_rule(event)
            return {"event_id": event.event_id, "rule_id": rule.rule_id}
        event = self.kernel.record("correction", text, case_id=None, source="bewohner")
        governance = self.kernel.record(
            "policy_violation",
            text,
            case_id=None,
            source="bewohner",
            payload={"forbidden_actions": [forbidden_action]},
        )
        return {"event_id": event.event_id, "governance_event_id": governance.event_id}

    def _compile_house_rule(self, event: Any) -> Rule:
        spec = event.payload["house_rule"]
        rule = Rule(
            rule_id=f"haus_{spec['forbidden_action']}_{event.event_id[-6:]}",
            condition=dict(spec["when"]),
            check={
                "must_not_contain": {
                    "field": "action_type",
                    "values": [spec["forbidden_action"]],
                }
            },
            severity=spec.get("severity", "high"),
            message=f"Hausregel: {event.content}",
            source=event.event_id,
        )
        self.kernel.checker.add_rule(rule)
        return rule

    def set_mode(self, room: str, statement: str, scope: list[str] | None = None) -> dict[str, Any]:
        """Betriebsmodus festhalten (z. B. Winterbetrieb)."""
        event = self.kernel.record(
            "decision",
            statement,
            case_id=room,
            source="haus",
            payload={"scope": scope or ["betrieb"]},
        )
        cards = self.kernel.cards.active(case_id=room, include_global=False)
        return {"event_id": event.event_id, "card_id": cards[-1].card_id}

    def change_season(self, room: str, card_id: str, new_statement: str, valid_from: str) -> dict[str, Any]:
        """Saisonwechsel als Evolution Patch — der alte Modus bleibt Historie."""
        new_card = self.kernel.patch(
            EvolutionPatch(
                target_memory=card_id,
                patch_type="replace",
                new_value=new_statement,
                valid_from=valid_from,
            )
        )
        return {"card_id": new_card.card_id}

    # -- Status ----------------------------------------------------------------------

    def rooms(self) -> list[str]:
        seen = set(self._rooms)
        for event in self.kernel.events.query():
            if event.case_id:
                seen.add(event.case_id)
        return sorted(seen)

    def room_status(self, room: str) -> dict[str, Any]:
        cards = self.kernel.cards.active(case_id=room)
        events = self.kernel.events.query(case_id=room)
        return {
            "room": room,
            "memory_cards": [c.to_dict() for c in cards],
            "recent_events": [e.to_dict() for e in events[-15:]],
            "risks": sum(1 for c in cards if c.memory_type == "risk"),
            "lessons": sum(1 for c in cards if c.memory_type == "failure"),
        }

    def seed_demo(self) -> None:
        """Demo-Gedächtnis, damit das Haus sofort Charakter hat."""
        if self.kernel.events.query():
            return
        self.remember_failure(
            "bad",
            "Heizung auf 24° um 6:00 hat trotzdem zu kaltem Bad geführt.",
            signature="heizung_24_um_6",
            alternative="Vorlauf 30 Minuten früher starten (5:30, 22°).",
        )
        self.mark_fragile(
            "keller",
            "sicherung_hauptkasten",
            "Beim letzten Schalten fiel das halbe Haus aus.",
        )
        self.add_house_rule(
            "Nie staubsaugen, wenn jemand im Homeoffice-Call ist.",
            forbidden_action="staubsaugen",
            when={"call_aktiv": True},
        )
        self.add_house_rule(
            "Die Haustür wird nachts nie automatisch entriegelt.",
            forbidden_action="tuer_entriegeln",
        )
        self.set_mode("wohnzimmer", "Winterbetrieb: Heizung 21°, Rollläden ab 17 Uhr.")
