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
from brainfump.retrieval import Retriever, ScoredCard, Similarity
from brainfump.rules import RuleCompiler, RuntimeChecker
from brainfump.trust import TrustPolicy


class BrainFumpKernel:
    """Agentic Memory Kernel mit gemeinsamem Speicherort.

    base_path=None hält alles in-memory (Tests, Prototyping); ansonsten
    entstehen events.db, cards.db und patches.db unterhalb von base_path
    (entspricht vfs://events/ und vfs://memory/).
    """

    def __init__(
        self,
        base_path: str | None = None,
        similarity: "Similarity | None" = None,
        trust: "TrustPolicy | None" = None,
    ) -> None:
        if base_path is None:
            event_path = card_path = patch_path = ":memory:"
        else:
            os.makedirs(base_path, exist_ok=True)
            event_path = os.path.join(base_path, "events.db")
            card_path = os.path.join(base_path, "cards.db")
            patch_path = os.path.join(base_path, "patches.db")

        self.trust = trust or TrustPolicy()
        self.events = EventLog(event_path)
        self.cards = MemoryCardStore(card_path)
        self.extractor = MemoryExtractor(self.cards)
        self.evolution = EvolutionMemory(self.cards, patch_path)
        self.compiler = RuleCompiler()
        self.checker = RuntimeChecker()
        self.gatekeeper = MemoryGatekeeper(
            self.cards, self.checker, min_alternative_trust=self.trust.alternative_min
        )
        self.retriever = Retriever(self.cards, similarity=similarity)
        self.consolidator = Consolidator(self.cards)

        # Regeln leben im RAM — nach einem Neustart aus den persistierten
        # correction-Events rekompiliert, aber nur von berechtigten Quellen.
        for event in self.events.query(event_type="correction"):
            if not self.trust.may_enforce_rules(event.source):
                continue
            rule = self.compiler.compile_correction(event)
            if rule is not None:
                self.checker.add_rule(rule)

    # -- Schreiben -----------------------------------------------------------

    def record(self, event_type: str, content: str, **kwargs: Any) -> Event:
        """Event beweissicher loggen (immer — Provenienz/Audit), dann
        trust-gefiltert zu Memory Cards und Runtime-Regeln verdichten."""
        event = self.events.record(event_type, content, **kwargs)
        source_trust = self.trust.trust_of(event.source)

        # Globale DNA (case_id=None, policy_violation→governance) nur von
        # berechtigten Quellen. Untrusted: Event bleibt als Beweis, aber es
        # entsteht KEINE aktive systemweite Governance-Karte.
        if (
            event.case_id is None
            and event_type == "policy_violation"
            and not self.trust.may_write_global_dna(event.source)
        ):
            return event

        self.extractor.extract(event, trust=source_trust)

        # Korrekturen werden nur von berechtigten Quellen zu erzwungenen Regeln
        # (sonst bleiben sie eine reine Notiz/Memory Card, kein Qualitäts-Gate).
        if event_type == "correction" and self.trust.may_enforce_rules(event.source):
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
