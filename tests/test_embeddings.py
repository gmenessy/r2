"""Sprint 1 — Embedding-Provider, persistenter Cache, Parität & Latenz."""

import time

from brainfump.embeddings import HashingEmbedder, SqliteVectorCache
from brainfump.memory_cards import MemoryCard, MemoryCardStore
from brainfump.retrieval import EmbeddingSimilarity, LexicalSimilarity, Retriever, _cosine


def card(statement, **kw):
    return MemoryCard(memory_type=kw.pop("memory_type", "semantic"), statement=statement, **kw)


# -- HashingEmbedder ---------------------------------------------------------

def test_hashing_embedder_is_deterministic_and_fixed_dim():
    emb = HashingEmbedder(dim=64)
    a = emb("Zahlung über Stripe")
    b = emb("Zahlung über Stripe")
    assert a == b
    assert len(a) == 64


def test_hashing_embedder_is_l2_normalized():
    emb = HashingEmbedder(dim=128)
    vec = emb("frontend vue komponente")
    assert abs(sum(x * x for x in vec) - 1.0) < 1e-9


def test_hashing_embedder_empty_text():
    assert HashingEmbedder(dim=16)("") == [0.0] * 16


def test_hashing_embedder_semantic_proximity():
    emb = HashingEmbedder(dim=512)
    pay1 = emb("zahlung stripe gateway")
    pay2 = emb("stripe zahlung abwicklung")
    frontend = emb("vue react frontend komponente")
    assert _cosine(pay1, pay2) > _cosine(pay1, frontend)


# -- SqliteVectorCache -------------------------------------------------------

def test_vector_cache_set_get_len():
    cache = SqliteVectorCache()
    cache["k1"] = [1.0, 2.0, 3.0]
    assert cache["k1"] == [1.0, 2.0, 3.0]
    assert len(cache) == 1
    assert "k1" in cache


def test_vector_cache_persists_across_instances(tmp_path):
    path = str(tmp_path / "vectors.db")
    SqliteVectorCache(path)["card_42"] = [0.5, 0.5]
    assert SqliteVectorCache(path)["card_42"] == [0.5, 0.5]


# -- EmbeddingSimilarity mit persistentem Cache ------------------------------

def test_persistent_cache_avoids_reembedding_after_restart(tmp_path):
    path = str(tmp_path / "vectors.db")
    calls: list[str] = []

    def counting_embed(text):
        calls.append(text)
        return HashingEmbedder(dim=64)(text)

    c = card("Zahlung läuft über Stripe", case_id="a")

    sim1 = EmbeddingSimilarity(counting_embed, cache=SqliteVectorCache(path))
    sim1("query", c)
    embeds_first = [t for t in calls if "Zahlung" in t]
    assert len(embeds_first) == 1

    # "Neustart": neue Similarity-Instanz, gleicher persistenter Cache.
    sim2 = EmbeddingSimilarity(counting_embed, cache=SqliteVectorCache(path))
    sim2("andere query", c)
    embeds_total = [t for t in calls if "Zahlung" in t]
    assert len(embeds_total) == 1  # Card NICHT erneut eingebettet


def test_scope_change_triggers_reembed(tmp_path):
    """K5: Der Cache-Key umfasst Statement UND Scope. Eine reine Scope-
    Änderung muss neu eingebettet werden (sonst veralteter Vektor)."""
    cache = SqliteVectorCache(str(tmp_path / "vec.db"))
    sim = EmbeddingSimilarity(HashingEmbedder(dim=64), cache=cache)
    base = MemoryCard(memory_type="semantic", statement="Zahlung", case_id="a",
                      card_id="mem_x", scope=("alt",))
    changed = MemoryCard(memory_type="semantic", statement="Zahlung", case_id="a",
                         card_id="mem_x", scope=("neu",))
    sim("q", base)
    sim("q", changed)
    assert len(cache) == 2  # zwei verschiedene (statement+scope)-Hashes


def test_changed_statement_is_reembedded(tmp_path):
    path = str(tmp_path / "vectors.db")
    cache = SqliteVectorCache(path)
    sim = EmbeddingSimilarity(HashingEmbedder(dim=64), cache=cache)
    sim("q", card("alte Aussage", case_id="a", card_id="mem_x"))
    sim("q", card("neue Aussage", case_id="a", card_id="mem_x"))
    assert len(cache) == 2  # zwei verschiedene Statement-Hashes


# -- QM: Parität lexical <-> embedding auf Golden-Set ------------------------

def _golden_store():
    store = MemoryCardStore()
    store.add(card("Zahlung läuft über Stripe", case_id="a", scope=("zahlung",)))
    store.add(card("Frontend nutzt Vue Komponenten", case_id="a", scope=("frontend",)))
    store.add(card("Postgres Migration mit Index", case_id="a", scope=("datenbank",)))
    return store


def test_retrieval_parity_lexical_and_embedding():
    """Beide Verfahren liefern für klare Queries dieselbe Top-1-Karte."""
    store = _golden_store()
    lexical = Retriever(store, similarity=LexicalSimilarity())
    embedding = Retriever(store, similarity=EmbeddingSimilarity(HashingEmbedder(dim=512)))
    for query, needle in [
        ("Zahlung Stripe anpassen", "Stripe"),
        ("Vue Frontend Komponente", "Vue"),
        ("Postgres Index Migration", "Postgres"),
    ]:
        top_lex = lexical.search(query, case_id="a", k=1)[0].card.statement
        top_emb = embedding.search(query, case_id="a", k=1)[0].card.statement
        assert needle in top_lex
        assert needle in top_emb


# -- QM: Latenz-Smoke (p95) --------------------------------------------------

def test_embedding_retrieval_latency_smoke():
    store = MemoryCardStore()
    for i in range(500):
        store.add(card(f"thema {i % 5} variante {i}", case_id="a", scope=("thema",)))
    retriever = Retriever(store, similarity=EmbeddingSimilarity(HashingEmbedder(dim=128)))
    timings = []
    for _ in range(20):
        start = time.perf_counter()
        retriever.search("thema 2 variante", case_id="a", k=5)
        timings.append((time.perf_counter() - start) * 1000)
    p95 = sorted(timings)[int(0.95 * (len(timings) - 1))]
    # Großzügige Obergrenze (CI-Hardware variiert); fängt Größenordnungs-
    # Regressionen ab, ohne flaky zu sein.
    assert p95 < 250, f"p95={p95:.1f} ms über 500 Karten zu hoch"
