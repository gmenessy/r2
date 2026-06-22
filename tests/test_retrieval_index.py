"""Sprint 2 — Invertierter Token-Index als Kandidaten-Vorfilter.

Kernkriterium: ergebnis-identisch zum vollen Scan für die lexikalische
Similarity, aber nur über den kleinen Treffer-Satz gescort.
"""

import random

from brainfump.memory_cards import MemoryCard, MemoryCardStore
from brainfump.retrieval import EmbeddingSimilarity, LexicalSimilarity, Retriever
from brainfump.embeddings import HashingEmbedder


def card(statement, **kw):
    return MemoryCard(memory_type=kw.pop("memory_type", "semantic"), statement=statement, **kw)


def _populate_random(store, n, seed=1):
    rng = random.Random(seed)
    vocab = "zahlung stripe frontend vue react postgres index auth token docker compose".split()
    for i in range(n):
        words = rng.sample(vocab, k=rng.randint(2, 5))
        store.add(
            card(
                " ".join(words) + f" {i}",
                case_id=rng.choice(["a", "b", None]),
                scope=tuple(words[:1]),
            )
        )


def test_index_scores_same_cards_as_full_scan():
    """Der Vorfilter scort exakt dieselbe Kartenmenge mit denselben Scores.

    Verglichen wird die Menge gescorter Karten und ihr Score (auf 6 Stellen);
    die Mikro-Reihenfolge bei quasi gleichem Score ist nicht vergleichbar, da
    ``recency`` über ``datetime.now()`` zwischen zwei Aufrufen leicht driftet
    — das ist unabhängig vom Index.
    """
    store = MemoryCardStore()
    _populate_random(store, 300)
    indexed = Retriever(store, similarity=LexicalSimilarity(), use_index=True)
    full = Retriever(store, similarity=LexicalSimilarity(), use_index=False)
    rng = random.Random(7)
    vocab = "zahlung stripe frontend vue react postgres index auth token docker".split()
    for _ in range(25):
        query = " ".join(rng.sample(vocab, k=rng.randint(1, 3)))
        for case_id in ("a", "b", None):
            # k hoch genug, um die volle Treffermenge zu vergleichen.
            di = {s.card.card_id: round(s.score, 6) for s in indexed.search(query, case_id=case_id, k=9999)}
            df = {s.card.card_id: round(s.score, 6) for s in full.search(query, case_id=case_id, k=9999)}
            assert di == df


def test_index_invalidated_on_add():
    store = MemoryCardStore()
    store.add(card("Zahlung Stripe", case_id="a", scope=("zahlung",)))
    retriever = Retriever(store, similarity=LexicalSimilarity())
    assert len(retriever.search("Zahlung", case_id="a")) == 1
    # Neue Karte muss nach dem (bereits gebauten) Index sofort auffindbar sein.
    store.add(card("Zahlung Rückerstattung", case_id="a", scope=("zahlung",)))
    assert len(retriever.search("Zahlung", case_id="a")) == 2


def test_index_invalidated_on_status_change():
    store = MemoryCardStore()
    c = store.add(card("Zahlung Stripe", case_id="a", scope=("zahlung",)))
    retriever = Retriever(store, similarity=LexicalSimilarity())
    assert len(retriever.search("Zahlung", case_id="a")) == 1
    store.set_status(c.card_id, "archived")
    assert retriever.search("Zahlung", case_id="a") == []


def test_active_by_tokens_respects_case_and_date():
    store = MemoryCardStore()
    store.add(card("Zahlung Stripe", case_id="a", scope=("zahlung",)))
    store.add(card("Zahlung global", case_id=None, scope=("zahlung",)))
    store.add(card("Zahlung fremd", case_id="b", scope=("zahlung",)))
    # case a + globale DNA, fremde Akte b ausgeschlossen
    cards = store.active_by_tokens({"zahlung"}, case_id="a")
    statements = {c.statement for c in cards}
    assert statements == {"Zahlung Stripe", "Zahlung global"}


def test_only_lexical_exposes_candidate_tokens():
    """Nur die token-basierte Similarity erlaubt den exakten Vorfilter;
    Embeddings nicht → Retriever fällt dort auf den vollen Scan zurück."""
    assert hasattr(LexicalSimilarity(), "candidate_tokens")
    assert not hasattr(EmbeddingSimilarity(HashingEmbedder(dim=64)), "candidate_tokens")


def test_embedding_search_works_via_full_scan():
    store = MemoryCardStore()
    store.add(card("Zahlung Stripe", case_id="a", scope=("zahlung",)))
    retriever = Retriever(store, similarity=EmbeddingSimilarity(HashingEmbedder(dim=128)))
    assert retriever.search("Zahlung Stripe Gateway", case_id="a")
