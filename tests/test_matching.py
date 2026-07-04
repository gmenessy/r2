"""Semantisches (ontologisches) Action-Intent-Matching + Embedding-Adapter."""

from brainfump.embeddings import HashingEmbedder
from brainfump.matching import ActionMatcher, EmbeddingIntentMatcher, IntentMatcher
from brainfump.memory_cards import MemoryCard, MemoryCardStore
from brainfump.gatekeeper import GateMode, MemoryGatekeeper


# Ein synonym-fähiges Test-Embedding (Konzept-Vektoren), das ein echtes
# Sprachmodell simuliert: Synonyme aktivieren dieselbe Dimension.
_CONCEPTS = {
    "destroy": {"delete", "drop", "purge", "eliminate", "destroy", "wipe", "remove"},
    "read": {"read", "inspect", "view", "get", "metrics"},
    "prod": {"prod", "production", "prd", "live"},
    "records": {"records", "data", "rows", "cache"},
}


def concept_embed(text):
    toks = set(text.lower().split())
    return [1.0 if (toks & words) else 0.0 for words in _CONCEPTS.values()]


def test_intent_matches_verb_synonyms_and_resource():
    m = IntentMatcher()
    intent = {"verb": "destroy", "resource": "prod"}
    for act in ("delete_production_data", "drop_prod_tables", "eliminate_prod_records",
                "purge_prod_cache", "wipe_prd_volume"):
        assert m.matches(act, intent), act


def test_intent_requires_both_verb_and_resource_when_given():
    m = IntentMatcher()
    intent = {"verb": "destroy", "resource": "prod"}
    # destroy-Verb, aber keine prod-Ressource → kein Match.
    assert not m.matches("delete_temp_cache", intent)
    # prod-Ressource, aber kein destroy-Verb → kein Match.
    assert not m.matches("read_production_config", intent)


def test_intent_verb_only():
    m = IntentMatcher()
    assert m.matches("purge_anything", {"verb": "destroy"})
    assert not m.matches("inspect_anything", {"verb": "destroy"})


def test_empty_intent_never_matches():
    assert not IntentMatcher().matches("delete_prod", {})
    assert not IntentMatcher().matches(None, {"verb": "destroy"})


def test_custom_ontology():
    m = IntentMatcher(verbs={"yeet": {"yeet", "vaporize"}}, resources={"box": {"box"}})
    assert m.matches("vaporize_box", {"verb": "yeet", "resource": "box"})
    assert not m.matches("delete_prod", {"verb": "yeet", "resource": "box"})


def test_gatekeeper_blocks_via_intent():
    store = MemoryCardStore()
    store.add(MemoryCard(
        memory_type="governance", case_id=None,
        statement="Keine destruktiven Aktionen auf Produktionsdaten.",
        payload={"forbidden_intents": [{"verb": "destroy", "resource": "prod"}]},
    ))
    gate = MemoryGatekeeper(store)
    assert gate.check({"action_type": "eliminate_prod_records", "case_id": "a"}).mode == GateMode.BLOCK
    assert gate.check({"action_type": "read_prod_metrics", "case_id": "a"}).mode == GateMode.ALLOW


# -- EmbeddingIntentMatcher (Adapter) ----------------------------------------

def test_embedding_matcher_conforms_to_protocol():
    m = EmbeddingIntentMatcher(concept_embed)
    assert isinstance(m, ActionMatcher)
    assert isinstance(IntentMatcher(), ActionMatcher)


def test_embedding_matcher_catches_novel_synonym_with_real_like_model():
    m = EmbeddingIntentMatcher(concept_embed, threshold=0.6)
    intent = {"verb": "destroy", "resource": "prod"}
    # 'eliminate' steht in KEINER Regex/Liste — ein synonym-fähiges Modell
    # bettet es nahe an 'destroy' ein → Treffer.
    assert m.matches("eliminate_prod_records", intent)
    assert m.matches("wipe_production_rows", intent)
    # Harmlose Lese-Aktion bleibt unter der Schwelle.
    assert not m.matches("read_prod_metrics", intent)


def test_embedding_matcher_like_exemplars():
    m = EmbeddingIntentMatcher(concept_embed, threshold=0.6)
    intent = {"like": ["destroy production data"]}
    assert m.matches("purge_prod_records", intent)
    assert not m.matches("view_prod_dashboard", intent)


def test_embedding_matcher_honest_limit_with_lexical_hashing():
    """Ehrlichkeit: der rein lexikalische HashingEmbedder erkennt Synonymie
    NICHT — 'eliminate' teilt keine Tokens mit 'destroy'. Für ihn ist die
    Ontologie (IntentMatcher) die richtige Wahl, nicht dieser Adapter."""
    m = EmbeddingIntentMatcher(HashingEmbedder(dim=256), threshold=0.5)
    assert not m.matches("eliminate_prod_records", {"verb": "destroy", "resource": "prod"})
    # Zum Kontrast: die Ontologie fängt es sehr wohl.
    assert IntentMatcher().matches("eliminate_prod_records",
                                   {"verb": "destroy", "resource": "prod"})


def test_gatekeeper_with_embedding_matcher():
    store = MemoryCardStore()
    store.add(MemoryCard(
        memory_type="governance", case_id=None,
        statement="Keine destruktiven Aktionen auf Produktionsdaten.",
        payload={"forbidden_intents": [{"verb": "destroy", "resource": "prod"}]},
    ))
    gate = MemoryGatekeeper(store, action_matcher=EmbeddingIntentMatcher(concept_embed, threshold=0.6))
    assert gate.check({"action_type": "eliminate_prod_records", "case_id": "a"}).mode == GateMode.BLOCK
    assert gate.check({"action_type": "read_prod_metrics", "case_id": "a"}).mode == GateMode.ALLOW
