"""E-2 — Memory Graph (A-MEM/Zettelkasten-Schicht)."""

import pytest

from brainfump import BrainFumpKernel
from brainfump.evolution import EvolutionPatch
from brainfump.graph import MemoryGraph
from brainfump.memory_cards import MemoryCard


# -- MemoryGraph (Einheit) ---------------------------------------------------

def test_add_and_query_edges():
    g = MemoryGraph()
    g.add_edge("a", "b", "depends_on")
    g.add_edge("b", "c", "depends_on")
    assert len(g) == 2
    assert g.neighbors("b") == ["a", "c"]
    assert g.neighbors("b", direction="out") == ["c"]
    assert g.neighbors("b", direction="in") == ["a"]


def test_unknown_edge_type_rejected():
    with pytest.raises(ValueError):
        MemoryGraph().add_edge("a", "b", "vibes_with")


def test_reachable_follows_chain():
    g = MemoryGraph()
    g.add_edge("a", "b", "depends_on")
    g.add_edge("b", "c", "depends_on")
    g.add_edge("c", "d", "depends_on")
    assert g.reachable("a", "depends_on", direction="out") == ["b", "c", "d"]


def test_remove_node():
    g = MemoryGraph()
    g.add_edge("a", "b", "relates_to")
    g.add_edge("b", "c", "relates_to")
    assert g.remove_node("b") == 2
    assert len(g) == 0


def test_edge_meta_roundtrip():
    g = MemoryGraph()
    g.add_edge("a", "b", "contradicts", meta={"winner": "a", "loser": "b"})
    e = g.edges_of("a", rel="contradicts")[0]
    assert e.meta == {"winner": "a", "loser": "b"}


# -- Kernel-Integration ------------------------------------------------------

def test_evolution_writes_supersedes_and_exception_edges():
    kernel = BrainFumpKernel()
    kernel.record("decision", "Vanilla JS only", case_id="a", payload={"scope": ["frontend"]})
    base = kernel.cards.active(case_id="a", include_global=False)[0]

    kernel.patch(EvolutionPatch(target_memory=base.card_id, patch_type="scope_exception",
                                new_value="Vue fürs Admin-Backend", scope=("admin",),
                                valid_from="2026-06-01"))
    new = kernel.patch(EvolutionPatch(target_memory=base.card_id, patch_type="replace",
                                      new_value="Svelte projektweit", valid_from="2026-07-01"))

    # supersedes: neue Version → alte
    sup = kernel.graph.edges_of(new.card_id, rel="supersedes", direction="out")
    assert sup and sup[0].dst == base.card_id
    # exception_to: Ausnahme → Grundregel
    exc = kernel.graph.edges_of(base.card_id, rel="exception_to", direction="in")
    assert exc and exc[0].rel == "exception_to"


def test_consolidation_writes_contradiction_edge_with_winner():
    kernel = BrainFumpKernel()
    # Zwei widersprechende Karten unterschiedlichen Vertrauens.
    kernel.cards.add(MemoryCard(memory_type="semantic", statement="React nutzen",
                                case_id="a", scope=("fe",), trust=1.0))
    kernel.cards.add(MemoryCard(memory_type="semantic", statement="Kein React nutzen",
                                case_id="a", scope=("fe",), trust=0.2))
    kernel.consolidate(case_id="a")
    edges = kernel.graph.all_edges(rel="contradicts")
    assert len(edges) == 1
    assert edges[0].meta["winner"] is not None
    assert edges[0].meta["loser"] is not None


def test_kernel_link_related_and_explain():
    kernel = BrainFumpKernel()
    kernel.record("decision", "Service A", case_id="a")
    kernel.record("decision", "Service B", case_id="a")
    a, b = kernel.cards.active(case_id="a", include_global=False)
    kernel.link(a.card_id, b.card_id, "depends_on", reason="A ruft B")

    related = kernel.related(a.card_id, rel="depends_on", direction="out")
    assert [c.card_id for c in related] == [b.card_id]

    why = kernel.explain(a.card_id)
    assert why[0]["rel"] == "depends_on"
    assert why[0]["other_statement"] == "Service B"
    assert why[0]["meta"]["reason"] == "A ruft B"


def test_graph_persists_across_restart(tmp_path):
    path = str(tmp_path / "mem")
    k1 = BrainFumpKernel(path)
    k1.record("decision", "A", case_id="a")
    k1.record("decision", "B", case_id="a")
    a, b = k1.cards.active(case_id="a", include_global=False)
    k1.link(a.card_id, b.card_id, "depends_on")

    k2 = BrainFumpKernel(path)
    assert len(k2.graph) == 1
    assert k2.graph.neighbors(a.card_id, direction="out") == [b.card_id]
