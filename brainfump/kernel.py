"""BrainFump Kernel — Fassade über alle Module.

Verdrahtet die Systempipeline aus Abschnitt 5:

    Event → Event Log → Memory Extractor → Memory Cards
          → Evolution Patch Check → Consolidation
          → fRAG Retrieval → Rule Compiler → Gatekeeper
          → Agent Action → Audit + Evaluation
"""

from __future__ import annotations

import os
from typing import Any

from brainfump.consolidation import ConsolidationReport, Consolidator
from brainfump.events import Event, EventLog
from brainfump.evaluation import EvaluationHarness, EvaluationReport, GoldenScenario
from brainfump.evolution import EvolutionMemory, EvolutionPatch
from brainfump.extractor import MemoryExtractor
from brainfump.gatekeeper import GateDecision, MemoryGatekeeper
from brainfump.memory_cards import MemoryCard, MemoryCardStore
from brainfump.retrieval import Retriever, ScoredCard
from brainfump.rules import RuleCompiler, RuntimeChecker


class BrainFumpKernel:
    """Agentic Memory Kernel mit gemeinsamem Speicherort.

    base_path=None hält alles in-memory (Tests, Prototyping); ansonsten
    entstehen events.db, cards.db und patches.db unterhalb von base_path
    (entspricht vfs://events/ und vfs://memory/).
    """

    def __init__(self, base_path: str | None = None) -> None:
        if base_path is None:
            event_path = card_path = patch_path = ":memory:"
        else:
            os.makedirs(base_path, exist_ok=True)
            event_path = os.path.join(base_path, "events.db")
            card_path = os.path.join(base_path, "cards.db")
            patch_path = os.path.join(base_path, "patches.db")

        self.events = EventLog(event_path)
        self.cards = MemoryCardStore(card_path)
        self.extractor = MemoryExtractor(self.cards)
        self.evolution = EvolutionMemory(self.cards, patch_path)
        self.compiler = RuleCompiler()
        self.checker = RuntimeChecker()
        self.gatekeeper = MemoryGatekeeper(self.cards, self.checker)
        self.retriever = Retriever(self.cards)
        self.consolidator = Consolidator(self.cards)

        # Regeln leben im RAM — nach einem Neustart werden sie aus den
        # persistierten correction-Events deterministisch rekompiliert.
        for event in self.events.query(event_type="correction"):
            rule = self.compiler.compile_correction(event)
            if rule is not None:
                self.checker.add_rule(rule)

    # -- Schreiben -----------------------------------------------------------

    def record(self, event_type: str, content: str, **kwargs: Any) -> Event:
        """Event beweissicher loggen, Memory Card extrahieren und
        Korrekturen sofort in Runtime-Regeln kompilieren."""
        event = self.events.record(event_type, content, **kwargs)
        self.extractor.extract(event)
        rule = self.compiler.compile_correction(event)
        if rule is not None:
            self.checker.add_rule(rule)
        return event

    def patch(self, patch: EvolutionPatch) -> MemoryCard | None:
        return self.evolution.apply_patch(patch)

    # -- Lesen ----------------------------------------------------------------

    def search(self, query: str, case_id: str | None = None, k: int = 5) -> list[ScoredCard]:
        return self.retriever.search(query, case_id=case_id, k=k)

    # -- Runtime ---------------------------------------------------------------

    def check_action(self, action: dict[str, Any]) -> GateDecision:
        """Memory Gatekeeper: Pre-Action Check; die Entscheidung wird als
        agent_action-Event auditierbar protokolliert."""
        decision = self.gatekeeper.check(action)
        self.events.record(
            "agent_action",
            f"gatekeeper:{decision.mode.label}",
            case_id=action.get("case_id"),
            source="gatekeeper",
            payload={"action": action, "decision": decision.to_dict()},
        )
        return decision

    # -- Offline ---------------------------------------------------------------

    def consolidate(self, case_id: str | None = None) -> ConsolidationReport:
        return self.consolidator.consolidate(case_id=case_id)

    def evaluate(self, scenarios: list[GoldenScenario]) -> EvaluationReport:
        return EvaluationHarness(self.gatekeeper, self.cards).run(scenarios)
