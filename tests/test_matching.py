"""Semantisches (ontologisches) Action-Intent-Matching."""

from brainfump.matching import IntentMatcher
from brainfump.memory_cards import MemoryCard, MemoryCardStore
from brainfump.gatekeeper import GateMode, MemoryGatekeeper


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
