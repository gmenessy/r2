"""E-3 — Wiki-Projektion (menschenlesbare Memory-Seiten pro Akte)."""

from brainfump import BrainFumpKernel, WikiProjection
from brainfump.evolution import EvolutionPatch


def _seed(kernel):
    kernel.record("decision", "Frontend bleibt Vanilla JS.", case_id="a",
                  payload={"scope": ["frontend"]})
    base = kernel.cards.active(case_id="a", include_global=False)[0]
    kernel.patch(EvolutionPatch(target_memory=base.card_id, patch_type="replace",
                                new_value="Migration auf Svelte.", valid_from="2026-07-01"))
    kernel.record("risk_marker", "Zahlungsmodul ist fragil.", case_id="a",
                  payload={"scope": ["payment"]})
    return base


def test_render_case_contains_sections_and_relationships():
    kernel = BrainFumpKernel()
    _seed(kernel)
    md = kernel.wiki_page("a")
    assert "# Akte: a" in md
    assert "nicht die alleinige Wahrheit" in md
    assert "## Entscheidungen & Wissen" in md
    assert "Migration auf Svelte." in md
    assert "## Risiken" in md
    assert "Zahlungsmodul ist fragil." in md
    # Beziehung aus dem Graph (supersedes → 'ersetzt').
    assert "## Beziehungen" in md
    assert "ersetzt" in md
    # Verlauf ist revisionssicher: auch die ersetzte Ur-Entscheidung steht drin.
    assert "Frontend bleibt Vanilla JS." in md


def test_superseded_card_not_in_active_knowledge_but_in_history():
    kernel = BrainFumpKernel()
    _seed(kernel)
    md = kernel.wiki_page("a")
    knowledge = md.split("## Beziehungen")[0]
    # Die alte Version taucht im aktiven Wissen NICHT mehr auf …
    assert "Frontend bleibt Vanilla JS." not in knowledge
    # … wohl aber im Verlauf.
    assert "Frontend bleibt Vanilla JS." in md


def test_render_index_lists_cases():
    kernel = BrainFumpKernel()
    kernel.record("decision", "x", case_id="akte_1")
    kernel.record("decision", "y", case_id="akte_2")
    idx = kernel.wiki_index()
    assert "# Memory-Wiki" in idx
    assert "**akte_1**" in idx and "**akte_2**" in idx


def test_empty_case_renders_gracefully():
    kernel = BrainFumpKernel()
    md = kernel.wiki_page("leer")
    assert "# Akte: leer" in md
    assert "Noch kein aktives Wissen" in md


def test_projection_is_read_only():
    """Die Projektion verändert den Zustand nicht (nur Lesen)."""
    kernel = BrainFumpKernel()
    _seed(kernel)
    before_cards = len(kernel.cards)
    before_events = len(kernel.events)
    WikiProjection(kernel).render_case("a")
    WikiProjection(kernel).render_index()
    assert len(kernel.cards) == before_cards
    assert len(kernel.events) == before_events


def test_contradiction_shows_confirmed_winner():
    from brainfump.memory_cards import MemoryCard
    kernel = BrainFumpKernel()
    kernel.cards.add(MemoryCard(memory_type="semantic", statement="React nutzen",
                                case_id="a", scope=("fe",), trust=1.0))
    kernel.cards.add(MemoryCard(memory_type="semantic", statement="Kein React nutzen",
                                case_id="a", scope=("fe",), trust=0.2))
    kernel.consolidate(case_id="a")
    md = kernel.wiki_page("a")
    assert "widerspricht" in md
    assert "bestätigt:" in md
