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
from brainfump.graph import Edge, MemoryGraph
from brainfump.rules import RuleCompiler, RuleStore, RuntimeChecker
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
            event_path = card_path = patch_path = rule_path = graph_path = ":memory:"
        else:
            os.makedirs(base_path, exist_ok=True)
            event_path = os.path.join(base_path, "events.db")
            card_path = os.path.join(base_path, "cards.db")
            patch_path = os.path.join(base_path, "patches.db")
            rule_path = os.path.join(base_path, "rules.db")
            graph_path = os.path.join(base_path, "graph.db")

        self.trust = trust or TrustPolicy()
        self.events = EventLog(event_path)
        self.cards = MemoryCardStore(card_path)
        self.graph = MemoryGraph(graph_path)
        self.extractor = MemoryExtractor(self.cards)
        self.evolution = EvolutionMemory(self.cards, patch_path, graph=self.graph)
        self.compiler = RuleCompiler()
        self.checker = RuntimeChecker()
        self.rule_store = RuleStore(rule_path)
        self.gatekeeper = MemoryGatekeeper(
            self.cards, self.checker, min_alternative_trust=self.trust.alternative_min
        )
        self.retriever = Retriever(self.cards, similarity=similarity)
        self.consolidator = Consolidator(self.cards, graph=self.graph)

        # E-1: Regeln kommen aus dem persistenten RuleStore, nicht aus
        # wiederholter Ableitung. Einmaliger, idempotenter Backfill seedet den
        # Store aus berechtigten correction-Events (revoked Regeln bleiben
        # revoked, da add() INSERT OR IGNORE nutzt); danach lädt der Checker die
        # aktiven Regeln.
        for event in self.events.query(event_type="correction"):
            if not self.trust.may_enforce_rules(event.source):
                continue
            rule = self.compiler.compile_correction(event)
            if rule is not None:
                self.rule_store.add(rule, source=event.source)
        self._reload_rules()

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
        # Die Regel wird persistiert UND live in den Checker gehängt.
        if event_type == "correction" and self.trust.may_enforce_rules(event.source):
            rule = self.compiler.compile_correction(event)
            if rule is not None and not self.rule_store.exists(rule.rule_id):
                self.rule_store.add(rule, source=event.source)
                self.checker.add_rule(rule)
        return event

    def revoke_rule(self, rule_id: str) -> bool:
        """Eine erzwungene Regel zurücknehmen (Status 'revoked'). Sie wird aus
        der Live-Prüfung entfernt und beim nächsten Backfill nicht wiederbelebt.
        Das append-only Event Log bleibt unangetastet."""
        revoked = self.rule_store.revoke(rule_id)
        if revoked:
            self._reload_rules()
        return revoked

    def _reload_rules(self) -> None:
        """Live-Checker aus den aktiven Regeln des Stores neu bestücken."""
        self.checker.rules = list(self.rule_store.active())

    def patch(self, patch: EvolutionPatch) -> MemoryCard | None:
        return self.evolution.apply_patch(patch)

    def link(self, src_card_id: str, dst_card_id: str, rel: str = "depends_on", **meta: Any) -> Edge:
        """Explizite Beziehung zwischen zwei Memory Cards deklarieren
        (z. B. eine Abhängigkeit), die im Graph traversierbar wird."""
        return self.graph.add_edge(src_card_id, dst_card_id, rel, meta=meta or None)

    # -- Lesen ----------------------------------------------------------------

    def search(self, query: str, case_id: str | None = None, k: int = 5) -> list[ScoredCard]:
        return self.retriever.search(query, case_id=case_id, k=k)

    def related(self, card_id: str, rel: str | None = None, direction: str = "both") -> list[MemoryCard]:
        """Nachbar-Karten von ``card_id`` im Memory Graph (optional nach
        Relationstyp/Richtung), als aufgelöste MemoryCards."""
        cards = [self.cards.get(nid) for nid in self.graph.neighbors(card_id, rel=rel, direction=direction)]
        return [c for c in cards if c is not None]

    def wiki_page(self, case_id: str, max_events: int = 15) -> str:
        """Menschenlesbare Markdown-Projektion einer Akte (Spec §7)."""
        from brainfump.wiki import WikiProjection

        return WikiProjection(self).render_case(case_id, max_events=max_events)

    def wiki_index(self) -> str:
        """Markdown-Index aller Akten."""
        from brainfump.wiki import WikiProjection

        return WikiProjection(self).render_index()

    def explain(self, card_id: str) -> list[dict[str, Any]]:
        """Menschenlesbare Begründung: welche Beziehungen den Zustand einer
        Karte beeinflusst haben (Supersedes, Widersprüche, Ausnahmen …)."""
        out: list[dict[str, Any]] = []
        for edge in self.graph.edges_of(card_id):
            other_id = edge.dst if edge.src == card_id else edge.src
            other = self.cards.get(other_id)
            out.append({
                "rel": edge.rel,
                "direction": "out" if edge.src == card_id else "in",
                "other_id": other_id,
                "other_statement": other.statement if other else None,
                "meta": edge.meta,
            })
        return out

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
