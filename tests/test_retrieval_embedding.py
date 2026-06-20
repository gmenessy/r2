"""Tests für den austauschbaren fRAG-Similarity-Slot (Ticket M-5)."""

import math

from brainfump.memory_cards import MemoryCard, MemoryCardStore
from brainfump.retrieval import (
    EmbeddingSimilarity,
    LexicalSimilarity,
    Retriever,
    _cosine,
)
from brainfump import BrainFumpKernel


def card(statement, **kw):
    return MemoryCard(memory_type=kw.pop("memory_type", "semantic"), statement=statement, **kw)


def test_lexical_is_default_and_unchanged():
    store = MemoryCardStore()
    store.add(card("Frontend bleibt Vanilla JS", case_id="a", scope=("frontend",)))
    retriever = Retriever(store)
    assert isinstance(retriever.similarity, LexicalSimilarity)
    assert retriever.search("Frontend Framework", case_id="a")  # findet etwas


def test_lexical_similarity_jaccard_value():
    sim = LexicalSimilarity()
    c = card("alpha beta gamma")
    # query {alpha, beta} ∩ card {alpha, beta, gamma} = 2 / |{alpha,beta,gamma}| = 2/3
    assert math.isclose(sim("alpha beta", c), 2 / 3)
    assert sim("völlig anderes", c) == 0.0


def test_cosine_helper():
    assert math.isclose(_cosine([1, 0], [1, 0]), 1.0)
    assert math.isclose(_cosine([1, 0], [0, 1]), 0.0)
    assert _cosine([0, 0], [1, 1]) == 0.0  # Nullvektor → 0, kein ZeroDivision


def _toy_embed(text: str):
    """Bag-of-Words-Embedding über ein winziges Vokabular (Testdouble)."""
    vocab = ["zahlung", "stripe", "frontend", "vue", "react", "fragil"]
    tokens = text.lower().split()
    return [float(tokens.count(word)) for word in vocab]


def test_embedding_similarity_matches_related_card():
    store = MemoryCardStore()
    payment = store.add(card("Zahlung läuft über Stripe", case_id="a"))
    frontend = store.add(card("Frontend nutzt Vue", case_id="a"))
    retriever = Retriever(store, similarity=EmbeddingSimilarity(_toy_embed))

    results = retriever.search("Zahlung Stripe anpassen", case_id="a")
    assert results[0].card.card_id == payment.card_id
    assert all(r.card.card_id != frontend.card_id for r in results) or results[-1].card.card_id == frontend.card_id


def test_embedding_caches_card_vectors():
    calls: list[str] = []

    def counting_embed(text: str):
        calls.append(text)
        return _toy_embed(text)

    sim = EmbeddingSimilarity(counting_embed)
    c = card("Zahlung Stripe")
    sim("query eins", c)
    sim("query zwei", c)
    # Card wird genau einmal eingebettet, jede Query einzeln.
    card_embeds = [t for t in calls if "Zahlung" in t]
    assert len(card_embeds) == 1
    assert calls.count("query eins") == 1 and calls.count("query zwei") == 1


def test_min_similarity_filters_noise():
    store = MemoryCardStore()
    store.add(card("Zahlung Stripe", case_id="a"))
    # Hohe Schwelle: nur sehr ähnliche Karten überleben.
    strict = Retriever(store, similarity=EmbeddingSimilarity(_toy_embed), min_similarity=0.99)
    assert strict.search("frontend vue", case_id="a") == []


def test_kernel_accepts_similarity_injection():
    kernel = BrainFumpKernel(similarity=EmbeddingSimilarity(_toy_embed))
    kernel.record("decision", "Zahlung läuft über Stripe", case_id="a")
    assert isinstance(kernel.retriever.similarity, EmbeddingSimilarity)
    assert kernel.search("Zahlung Stripe", case_id="a")
