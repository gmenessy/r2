"""Sprint 2 — Memory Gatekeeper.

Der wichtigste Baustein (Abschnitt 4.5 und One-More-Thing): Vor jeder
kritischen Aktion wird gefragt, welche Memories, Regeln, Risiken und
früheren Fehler diese Aktion beeinflussen müssen. Der Agent darf nicht nur
wissen, was früher passiert ist — er muss daraus Konsequenzen ziehen,
bevor er handelt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from brainfump.memory_cards import MemoryCardStore
from brainfump.rules import RuntimeChecker


class GateMode(IntEnum):
    """Modi geordnet nach Eingriffstiefe; der höchste Treffer gewinnt."""

    ALLOW = 0
    WARN = 1
    REQUIRE_REVIEW = 2
    SUGGEST_ALTERNATIVE = 3
    BLOCK = 4

    @property
    def label(self) -> str:
        return self.name.lower()


_SEVERITY_TO_MODE = {
    "info": GateMode.ALLOW,
    "warning": GateMode.WARN,
    "high": GateMode.BLOCK,
    "critical": GateMode.BLOCK,
}


@dataclass(frozen=True)
class GateFinding:
    gate: str
    severity: str
    message: str
    source: str | None = None


@dataclass(frozen=True)
class GateDecision:
    mode: GateMode
    findings: tuple[GateFinding, ...] = ()
    consulted_memories: tuple[str, ...] = ()
    suggested_alternative: str | None = None

    @property
    def allowed(self) -> bool:
        return self.mode != GateMode.BLOCK

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.label,
            "allowed": self.allowed,
            "findings": [
                {
                    "gate": f.gate,
                    "severity": f.severity,
                    "message": f.message,
                    "source": f.source,
                }
                for f in self.findings
            ],
            "consulted_memories": list(self.consulted_memories),
            "suggested_alternative": self.suggested_alternative,
        }


class MemoryGatekeeper:
    """Pre-Action Gate über Memory Cards und kompilierten Runtime-Regeln.

    Eine Aktion wird als dict beschrieben, z. B.:
        {
            "action_type": "apply_fix",
            "case_id": "akte_42",
            "error_signature": "TypeError: x is undefined",
            "files": ["src/payment.py"],
            "context": {"project_type": "mvp_frontend", "package_json": {...}},
        }
    """

    def __init__(self, store: MemoryCardStore, checker: RuntimeChecker | None = None) -> None:
        self.store = store
        self.checker = checker or RuntimeChecker()

    def check(self, action: dict[str, Any]) -> GateDecision:
        case_id = action.get("case_id")
        findings: list[GateFinding] = []
        consulted: list[str] = []
        alternative: str | None = None
        mode = GateMode.ALLOW

        # Case-bounded: nur Karten der Akte plus globale DNA werden konsultiert.
        cards = self.store.active(case_id=case_id)

        # Gate 1: no_repeat_failed_fix — bereits gescheiterte Fixes blockieren.
        signature = action.get("error_signature") or action.get("fix_signature")
        if signature:
            for card in cards:
                if card.memory_type != "failure":
                    continue
                card_sig = card.payload.get("error_signature") or card.payload.get(
                    "fix_signature"
                )
                if card_sig == signature:
                    consulted.append(card.card_id)
                    findings.append(
                        GateFinding(
                            gate="no_repeat_failed_fix",
                            severity="high",
                            message=(
                                "Dieser Fix ist bereits gescheitert: "
                                f"{card.statement}"
                            ),
                            source=card.card_id,
                        )
                    )
                    if card.payload.get("alternative"):
                        alternative = card.payload["alternative"]
                        mode = max(mode, GateMode.SUGGEST_ALTERNATIVE)
                    else:
                        mode = max(mode, GateMode.BLOCK)

        # Gate 2: fragile_file — riskante Dateien erfordern Review.
        touched = set(action.get("files", ()))
        if touched:
            for card in cards:
                if card.memory_type != "risk":
                    continue
                fragile = set(card.payload.get("fragile_files", ()))
                if card.payload.get("fragile_file"):
                    fragile.add(card.payload["fragile_file"])
                hits = sorted(touched & fragile)
                if hits:
                    consulted.append(card.card_id)
                    findings.append(
                        GateFinding(
                            gate="fragile_file",
                            severity="high",
                            message=f"Fragile Datei(en) betroffen: {hits} — {card.statement}",
                            source=card.card_id,
                        )
                    )
                    mode = max(mode, GateMode.REQUIRE_REVIEW)

        # Gate 3: governance — explizit verbotene Aktionen blockieren.
        action_type = action.get("action_type")
        for card in cards:
            if card.memory_type != "governance":
                continue
            forbidden = card.payload.get("forbidden_actions", ())
            if card.payload.get("forbidden_action"):
                forbidden = (*forbidden, card.payload["forbidden_action"])
            if action_type in forbidden:
                consulted.append(card.card_id)
                findings.append(
                    GateFinding(
                        gate="governance",
                        severity="critical",
                        message=f"Governance-Regel verletzt: {card.statement}",
                        source=card.card_id,
                    )
                )
                mode = max(mode, GateMode.BLOCK)

        # Gate 4: kompilierte Runtime-Regeln (TRACE) gegen den Aktionskontext.
        context = {**action.get("context", {})}
        context.setdefault("case_id", case_id)
        context.setdefault("action_type", action_type)
        for violation in self.checker.check(context):
            findings.append(
                GateFinding(
                    gate="runtime_rule",
                    severity=violation.severity,
                    message=violation.message,
                    source=violation.rule_id,
                )
            )
            mode = max(mode, _SEVERITY_TO_MODE[violation.severity])

        return GateDecision(
            mode=mode,
            findings=tuple(findings),
            consulted_memories=tuple(dict.fromkeys(consulted)),
            suggested_alternative=alternative,
        )
