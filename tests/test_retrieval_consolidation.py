"""fRAG Retrieval (4.6) und Dream/Consolidation Layer (4.7)."""

from brainfump.consolidation import Consolidator
from brainfump.memory_cards import MemoryCard, MemoryCardStore
from brainfump.retrieval import Retriever


def test_retrieval_ranks_case_memory_above_global():
    store = MemoryCardStore()
    case_card = store.add(
        MemoryCard(
            memory_type="preference",
            statement="Frontend bleibt Vanilla JS",
            case_id="akte_1",
            scope=("frontend",),
        )
    )
    global_card = store.add(
        MemoryCard(
            memory_type="semantic",
            statement="Frontend Projekte nutzen Vanilla JS",
            case_id=None,
        )
    )
    results = Retriever(store).search("Welches Frontend Framework?", case_id="akte_1")
    ids = [r.card.score if False else r.card.card_id for r in results]
    assert ids[0] == case_card.card_id
    assert global_card.card_id in ids


def test_retrieval_boosts_risk_and_governance():
    store = MemoryCardStore()
    plain = store.add(
        MemoryCard(memory_type="semantic", statement="Zahlung läuft über Stripe", case_id="akte_1")
    )
    risk = store.add(
        MemoryCard(
            memory_type="risk",
            statement="Zahlung Modul ist fragil",
            case_id="akte_1",
        )
    )
    results = Retriever(store).search("Zahlung anpassen", case_id="akte_1")
    assert results[0].card.card_id == risk.card_id
    assert results[1].card.card_id == plain.card_id


def test_retrieval_ignores_unrelated_cards():
    store = MemoryCardStore()
    store.add(MemoryCard(memory_type="semantic", statement="Kaffee ist alle", case_id="akte_1"))
    assert Retriever(store).search("Frontend Framework", case_id="akte_1") == []


def test_dedupe_merges_evidence():
    store = MemoryCardStore()
    store.add(
        MemoryCard(
            memory_type="preference",
            statement="Vanilla JS bevorzugt.",
            case_id="akte_1",
            evidence=("evt_1",),
            confidence=0.8,
        )
    )
    store.add(
        MemoryCard(
            memory_type="preference",
            statement="vanilla js bevorzugt",
            case_id="akte_1",
            evidence=("evt_2",),
            confidence=0.9,
        )
    )
    report = Consolidator(store).consolidate(case_id="akte_1")
    assert len(report.deduplicated) == 1
    active = store.active(case_id="akte_1", include_global=False)
    assert len(active) == 1
    assert set(active[0].evidence) == {"evt_1", "evt_2"}
    assert active[0].confidence == 0.9


def test_contradiction_detection_flags_both_cards():
    store = MemoryCardStore()
    a = store.add(
        MemoryCard(
            memory_type="preference",
            statement="React im Frontend verwenden",
            case_id="akte_1",
            scope=("frontend",),
        )
    )
    b = store.add(
        MemoryCard(
            memory_type="preference",
            statement="Kein React im Frontend verwenden",
            case_id="akte_1",
            scope=("frontend",),
        )
    )
    report = Consolidator(store).consolidate(case_id="akte_1")
    assert (a.card_id, b.card_id) in report.contradictions
    assert store.get(a.card_id).status == "contradicted"
    assert store.get(b.card_id).status == "contradicted"


def test_multiclause_statements_are_not_false_contradictions():
    """M7: Mehrsatz-Statements mit gemeinsamem Thema, aber nur teilweiser
    Überlappung dürfen NICHT als Widerspruch stillgelegt werden."""
    store = MemoryCardStore()
    a = store.add(
        MemoryCard(
            memory_type="semantic",
            statement="Frontend nutzt Vanilla JS und das Deployment läuft über Docker Compose",
            case_id="akte_1",
            scope=("frontend",),
        )
    )
    b = store.add(
        MemoryCard(
            memory_type="semantic",
            statement="Frontend Tests laufen nicht über Docker sondern lokal mit pytest",
            case_id="akte_1",
            scope=("frontend",),
        )
    )
    report = Consolidator(store).consolidate(case_id="akte_1")
    assert report.contradictions == []
    assert store.get(a.card_id).status == "active"
    assert store.get(b.card_id).status == "active"


def test_expired_cards_archived():
    store = MemoryCardStore()
    card = store.add(
        MemoryCard(
            memory_type="semantic",
            statement="API v1 verwenden",
            case_id="akte_1",
            valid_from="2025-01-01",
            valid_to="2025-12-31",
        )
    )
    report = Consolidator(store).consolidate(case_id="akte_1", today="2026-06-12")
    assert card.card_id in report.archived
    assert store.get(card.card_id).status == "archived"
