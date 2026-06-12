"""Sprint 5 — Evaluation Layer / Harness.

Memory wird eigenständig getestet (MemGym/EvoMemBench-Gedanke):
Wurde die richtige Erinnerung gefunden? War sie gültig? Hat sie eine
falsche Aktion verhindert? Hat sie unnötig blockiert?
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from brainfump.gatekeeper import GateDecision, GateMode
from brainfump.memory_cards import MemoryCard, MemoryCardStore


# ---------------------------------------------------------------------------
# Metriken (Abschnitt 4.8)
# ---------------------------------------------------------------------------

def memory_precision_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    top = retrieved[:k]
    if not top:
        return 0.0
    return sum(1 for card_id in top if card_id in relevant) / len(top)


def rule_compliance_rate(outcomes: list[bool]) -> float:
    """Anteil der Tasks, die alle Runtime-Regeln eingehalten haben."""
    if not outcomes:
        return 1.0
    return sum(outcomes) / len(outcomes)


def repeated_failure_avoidance(decisions: list[GateDecision]) -> float:
    """Anteil der Wiederholungsversuche, die das Gate gestoppt hat
    (block, suggest_alternative oder require_review)."""
    if not decisions:
        return 1.0
    stopped = sum(1 for d in decisions if d.mode >= GateMode.REQUIRE_REVIEW)
    return stopped / len(decisions)


def case_scope_leakage_rate(
    decisions: list[GateDecision], case_id: str, store: MemoryCardStore
) -> float:
    """Anteil konsultierter Memories, die weder zur Akte noch global sind."""
    consulted = [m for d in decisions for m in d.consulted_memories]
    if not consulted:
        return 0.0
    leaked = 0
    for card_id in consulted:
        card = store.get(card_id)
        if card is not None and card.case_id not in (case_id, None):
            leaked += 1
    return leaked / len(consulted)


def memory_freshness_score(cards: list[MemoryCard], half_life_days: float = 90.0) -> float:
    """Durchschnittliche Frische aktiver Karten (1.0 = brandneu)."""
    if not cards:
        return 0.0
    now = datetime.now(timezone.utc)
    total = 0.0
    for card in cards:
        created = datetime.fromisoformat(card.created_at)
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age_days = max((now - created).total_seconds() / 86400, 0.0)
        total += 0.5 ** (age_days / half_life_days)
    return total / len(cards)


def governance_false_positive_rate(
    decisions: list[tuple[GateDecision, bool]]
) -> float:
    """Anteil der Eingriffe (warn/review/block) bei Aktionen, die laut Label
    harmlos waren — misst, ob Memory unnötig blockiert."""
    interventions = [(d, harmful) for d, harmful in decisions if d.mode > GateMode.ALLOW]
    if not interventions:
        return 0.0
    false_positives = sum(1 for _, harmful in interventions if not harmful)
    return false_positives / len(interventions)


# ---------------------------------------------------------------------------
# Golden Scenarios
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GoldenScenario:
    """Ein erwartetes Gatekeeper-Verhalten für eine konkrete Aktion."""

    name: str
    action: dict[str, Any]
    expected_mode: GateMode
    harmful: bool = False  # War die Aktion tatsächlich schädlich?


@dataclass
class EvaluationReport:
    passed: int = 0
    failed: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        total = self.passed + self.failed
        return self.passed / total if total else 1.0


class EvaluationHarness:
    """Führt Golden Scenarios gegen einen Gatekeeper aus und berechnet
    die Memory-Metriken — als Regressionsschutz für das Memory-System."""

    def __init__(self, gatekeeper: Any, store: MemoryCardStore) -> None:
        self.gatekeeper = gatekeeper
        self.store = store

    def run(self, scenarios: list[GoldenScenario]) -> EvaluationReport:
        report = EvaluationReport()
        labeled: list[tuple[GateDecision, bool]] = []
        repeat_decisions: list[GateDecision] = []

        for scenario in scenarios:
            decision = self.gatekeeper.check(scenario.action)
            labeled.append((decision, scenario.harmful))
            if scenario.action.get("error_signature"):
                repeat_decisions.append(decision)
            if decision.mode == scenario.expected_mode:
                report.passed += 1
            else:
                report.failed += 1
                report.failures.append(
                    {
                        "scenario": scenario.name,
                        "expected": scenario.expected_mode.label,
                        "actual": decision.mode.label,
                        "findings": [f.message for f in decision.findings],
                    }
                )

        report.metrics = {
            "pass_rate": report.pass_rate,
            "repeated_failure_avoidance": repeated_failure_avoidance(repeat_decisions),
            "governance_false_positive_rate": governance_false_positive_rate(labeled),
            "memory_freshness_score": memory_freshness_score(
                self.store.active(on_date=date.today().isoformat())
            ),
        }
        return report
