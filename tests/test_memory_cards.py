"""Sprint 1: Memory Cards — typisiert, case-bounded, versionierbar."""

import pytest

from brainfump.memory_cards import MemoryCard, MemoryCardStore


def make_card(**kwargs):
    defaults = dict(
        memory_type="preference",
        statement="Für MVP-Frontends wird Vanilla JS bevorzugt.",
        scope=("frontend", "mvp"),
        case_id="akte_1",
        valid_from="2026-04-20",
        confidence=0.91,
    )
    defaults.update(kwargs)
    return MemoryCard(**defaults)


def test_add_and_get():
    store = MemoryCardStore()
    card = store.add(make_card())
    assert store.get(card.card_id) == card


def test_unknown_memory_type_rejected():
    with pytest.raises(ValueError):
        make_card(memory_type="vibes")


def test_case_bounded_retrieval_with_global_dna():
    store = MemoryCardStore()
    case_card = store.add(make_card(case_id="akte_1"))
    other = store.add(make_card(case_id="akte_2", statement="anderer Fall"))
    global_card = store.add(
        make_card(case_id=None, memory_type="governance", statement="Globale DNA-Regel")
    )

    cards = store.active(case_id="akte_1")
    ids = [c.card_id for c in cards]
    assert case_card.card_id in ids
    assert global_card.card_id in ids
    assert other.card_id not in ids
    # Reihenfolge gemäß Prinzip 3.3: Case Memory vor Global DNA.
    assert ids.index(case_card.card_id) < ids.index(global_card.card_id)


def test_scope_filter():
    store = MemoryCardStore()
    store.add(make_card(scope=("frontend",)))
    store.add(make_card(scope=("backend",), statement="Backend-Regel"))
    assert len(store.active(case_id="akte_1", scope="frontend")) == 1


def test_validity_window():
    card = make_card(valid_from="2026-01-01", valid_to="2026-06-01")
    assert card.is_valid_on("2026-03-01")
    assert not card.is_valid_on("2025-12-31")
    assert not card.is_valid_on("2026-06-01")


def test_supersede_keeps_history():
    store = MemoryCardStore()
    old = store.add(make_card())
    new = store.supersede(old.card_id, make_card(statement="Vue erlaubt für Admin-Backend.", valid_from="2026-06-12"))

    assert store.get(old.card_id).status == "superseded"
    assert store.get(old.card_id).valid_to == "2026-06-12"
    assert new.supersedes == old.card_id
    chain = store.history(new.card_id)
    assert [c.card_id for c in chain] == [old.card_id, new.card_id]


def test_successor_of():
    store = MemoryCardStore()
    old = store.add(make_card())
    new = store.supersede(old.card_id, make_card(statement="Nachfolger", valid_from="2026-06-12"))
    assert store.successor_of(old.card_id).card_id == new.card_id
    assert store.successor_of(new.card_id) is None


def test_dict_roundtrip():
    card = make_card(scope=("frontend", "mvp"), evidence=("evt_1", "evt_2"))
    restored = MemoryCard.from_dict(card.to_dict())
    assert restored == card
    assert isinstance(restored.scope, tuple)
    assert isinstance(restored.evidence, tuple)
