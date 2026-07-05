"""Memory Cockpit — die "One More Thing"-App.

Macht den sonst unsichtbaren Agentic-Memory-Kernel sichtbar und begreifbar:
ein interaktiver Wissensgraph (Karten als Knoten, typisierte Beziehungen als
Kanten), Klick-Details mit Provenienz/Begründung, die menschenlesbare
Wiki-Projektion — und ein LIVE-Gatekeeper: man beschreibt eine geplante Aktion
und sieht sofort, ob das Gedächtnis sie erlaubt, warnt oder blockiert.

Ein einziges Fenster für: Event Log, Evolution/Versionierung, Memory Graph,
Trust/Provenienz, Widerspruchsauflösung, Wiki und den Pre-Action Gate.
"""

from __future__ import annotations

from typing import Any

from brainfump import BrainFumpKernel, TrustPolicy
from brainfump.evolution import EvolutionPatch

_CASE = "projekt_aurora"

_TYPE_COLORS = {
    "semantic": "#4f9cf9",
    "preference": "#d29922",
    "risk": "#da3633",
    "failure": "#f85149",
    "governance": "#a371f7",
    "skill": "#2ea44f",
    "episodic": "#8b98a5",
    "evolution": "#56d364",
}


class Cockpit:
    def __init__(self, kernel: BrainFumpKernel) -> None:
        self.kernel = kernel

    # -- Graph ----------------------------------------------------------------

    def cases(self) -> list[str]:
        return sorted({e.case_id for e in self.kernel.events.query() if e.case_id})

    def graph_data(self, case_id: str) -> dict[str, Any]:
        """Knoten (Karten der Akte, inkl. superseded Endpunkte von Kanten) und
        typisierte Kanten aus dem Memory Graph."""
        nodes: dict[str, dict[str, Any]] = {}

        def add_node(card_id: str) -> bool:
            card = self.kernel.cards.get(card_id)
            if card is None or card.case_id != case_id:
                return False
            if card_id not in nodes:
                nodes[card_id] = {
                    "id": card.card_id,
                    "label": card.statement if len(card.statement) <= 48 else card.statement[:45] + "…",
                    "type": card.memory_type,
                    "status": card.status,
                    "trust": round(card.trust, 2),
                    "color": _TYPE_COLORS.get(card.memory_type, "#8b98a5"),
                }
            return True

        for card in self.kernel.cards.active(case_id=case_id, include_global=False):
            add_node(card.card_id)

        edges = []
        for e in self.kernel.graph.all_edges():
            if add_node(e.src) and add_node(e.dst):
                edges.append({"src": e.src, "dst": e.dst, "rel": e.rel, "meta": e.meta})

        return {"nodes": list(nodes.values()), "edges": edges}

    # -- Detail / Wiki --------------------------------------------------------

    def card_detail(self, card_id: str) -> dict[str, Any]:
        card = self.kernel.cards.get(card_id)
        if card is None:
            raise KeyError(card_id)
        return {"card": card.to_dict(), "relationships": self.kernel.explain(card_id)}

    def wiki(self, case_id: str) -> str:
        return self.kernel.wiki_page(case_id)

    def timeline(self, case_id: str) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.kernel.events.query(case_id=case_id)]

    # -- Live-Gatekeeper ------------------------------------------------------

    def simulate(self, case_id: str, action: dict[str, Any]) -> dict[str, Any]:
        # None-Werte entfernen: der Gatekeeper erwartet iterierbare files /
        # dict-Kontext, kein None.
        clean = {k: v for k, v in action.items() if v is not None}
        clean["case_id"] = case_id
        return self.kernel.check_action(clean).to_dict()

    # -- Demo-Seed ------------------------------------------------------------

    def seed_demo(self) -> None:
        """Reichhaltiges Szenario, das jede Kernel-Fähigkeit im Graphen zeigt."""
        if self.kernel.events.query(case_id=_CASE):
            return
        k = self.kernel

        # Architekturentscheidung + Evolution (Vanilla → Svelte) → supersedes-Kante
        k.record("decision", "Frontend-Framework: Vanilla JS (kein Build-Step).",
                 case_id=_CASE, source="architekt", payload={"scope": ["frontend"]})
        base = [c for c in k.cards.active(case_id=_CASE, include_global=False)
                if "Vanilla" in c.statement][0]
        k.patch(EvolutionPatch(target_memory=base.card_id, patch_type="scope_exception",
                               new_value="Vue erlaubt fürs Admin-Backend.", scope=("admin",),
                               valid_from="2026-04-01"))
        k.patch(EvolutionPatch(target_memory=base.card_id, patch_type="replace",
                               new_value="Frontend-Framework: Svelte (projektweit).",
                               valid_from="2026-07-01"))

        # Zwei Quellen widersprechen sich, unterschiedliches Vertrauen → contradicts-Kante
        k.record("decision", "Die Datenbank ist PostgreSQL.", case_id=_CASE, source="ops",
                 payload={"scope": ["db"]})
        k.record("decision", "Die Datenbank ist nicht PostgreSQL.", case_id=_CASE, source="praktikant",
                 payload={"scope": ["db"]})
        k.consolidate(case_id=_CASE)

        # Risiko + gescheiterter Fix (mit Alternative)
        k.record("risk_marker", "Zahlungsmodul payment.py ist fragil.", case_id=_CASE,
                 source="ops", payload={"scope": ["payment"], "fragile_files": ["payment.py"]})
        k.record("failed_attempt", "Retry-Loop behob den Gateway-Timeout nicht.",
                 case_id=_CASE, source="ops",
                 payload={"error_signature": "TimeoutError: gateway",
                          "alternative": "Circuit Breaker einbauen."})

        # Governance (globale DNA) + Korrektur → erzwungene Regel
        k.record("policy_violation", "Produktionsdaten nie destruktiv ändern.",
                 case_id=None, source="secops",
                 payload={"forbidden_intents": [{"verb": "destroy", "resource": "prod"}]})
        k.record("correction", "Keine react-Abhängigkeit im Frontend.", case_id=_CASE,
                 source="architekt", payload={"forbid_dependencies": ["react"], "scope": ["frontend"]})

        # Explizite Abhängigkeit zwischen zwei Karten → depends_on-Kante
        svelte = [c for c in k.cards.active(case_id=_CASE, include_global=False)
                  if "Svelte" in c.statement]
        payment = [c for c in k.cards.active(case_id=_CASE, include_global=False)
                   if "payment.py" in c.statement]
        if svelte and payment:
            k.link(payment[0].card_id, svelte[0].card_id, "depends_on", reason="UI ruft Payment-API")

    @property
    def demo_case(self) -> str:
        return _CASE


def build_cockpit(kernel: BrainFumpKernel | None = None) -> Cockpit:
    # Restriktive Trust-Policy, damit Provenienz im Graphen sichtbar wirkt.
    if kernel is None:
        policy = TrustPolicy(default=0.6).grant("ops", 1.0).grant("architekt", 0.95) \
            .grant("secops", 1.0).grant("praktikant", 0.2)
        kernel = BrainFumpKernel(trust=policy)
    cockpit = Cockpit(kernel)
    cockpit.seed_demo()
    return cockpit
