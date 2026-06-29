"""Sprint 3: Evolution Memory — Patches, Validity Resolver, Konflikte."""

import pytest

from brainfump.evolution import EvolutionMemory, EvolutionPatch
from brainfump.memory_cards import MemoryCard, MemoryCardStore


def make_evolution():
    store = MemoryCardStore()
    return store, EvolutionMemory(store)


def base_card(**kwargs):
    defaults = dict(
        memory_type="preference",
        statement="Vanilla JS only",
        case_id="akte_1",
        scope=("frontend",),
        valid_from="2026-04-20",
    )
    defaults.update(kwargs)
    return MemoryCard(**defaults)


def test_replace_patch_supersedes_old_version():
    store, evo = make_evolution()
    card = store.add(base_card())
    new = evo.apply_patch(
        EvolutionPatch(
            target_memory=card.card_id,
            patch_type="replace",
            old_value="Vanilla JS only",
            new_value="Vanilla JS für MVP, Vue erlaubt für Admin-Backend",
            valid_from="2026-06-12",
        )
    )
    assert store.get(card.card_id).status == "superseded"
    assert new.statement.startswith("Vanilla JS für MVP")
    assert new.supersedes == card.card_id
    assert evo.patches_for(card.card_id)[0].patch_type == "replace"


def test_validity_resolver_respects_date():
    store, evo = make_evolution()
    card = store.add(base_card())
    evo.apply_patch(
        EvolutionPatch(
            target_memory=card.card_id,
            patch_type="replace",
            new_value="Vue erlaubt",
            valid_from="2026-06-12",
        )
    )
    # Vor dem Patch gilt der alte Stand, danach der neue.
    before = evo.resolve(card.card_id, on_date="2026-05-01")
    after = evo.resolve(card.card_id, on_date="2026-06-15")
    assert before.statement == "Vanilla JS only"
    assert after.statement == "Vue erlaubt"


def test_scope_exception_patch():
    store, evo = make_evolution()
    card = store.add(base_card())
    evo.apply_patch(
        EvolutionPatch(
            target_memory=card.card_id,
            patch_type="scope_exception",
            new_value="Vue erlaubt für Admin-Backend",
            scope=("admin_backend",),
            valid_from="2026-06-12",
        )
    )
    # Grundregel bleibt aktiv …
    assert store.get(card.card_id).status == "active"
    # … aber im Ausnahme-Scope gilt die Exception.
    resolved = evo.resolve(card.card_id, on_date="2026-06-15", scope="admin_backend")
    assert resolved.statement == "Vue erlaubt für Admin-Backend"
    resolved_default = evo.resolve(card.card_id, on_date="2026-06-15", scope="frontend")
    assert resolved_default.statement == "Vanilla JS only"


def test_deprecate_patch_archives():
    store, evo = make_evolution()
    card = store.add(base_card())
    result = evo.apply_patch(
        EvolutionPatch(target_memory=card.card_id, patch_type="deprecate", valid_from="2026-06-12")
    )
    assert result is None
    assert store.get(card.card_id).status == "archived"
    assert evo.resolve(card.card_id, on_date="2026-07-01") is None


def test_patch_requires_new_value():
    with pytest.raises(ValueError):
        EvolutionPatch(target_memory="mem_x", patch_type="replace")


def test_apply_patch_rejects_superseded_target():
    """M4: replace/extend nur auf der aktiven Spitze, nie auf superseded
    Karten (sonst verzweigt die Versionskette)."""
    store, evo = make_evolution()
    card = store.add(base_card())
    evo.apply_patch(
        EvolutionPatch(target_memory=card.card_id, patch_type="replace",
                       new_value="v2", valid_from="2026-06-12")
    )
    # card ist jetzt superseded → erneuter replace darauf muss fehlschlagen.
    with pytest.raises(ValueError):
        evo.apply_patch(
            EvolutionPatch(target_memory=card.card_id, patch_type="replace",
                           new_value="v3", valid_from="2026-06-20")
        )


def test_resolve_scope_exception_does_not_leak_across_cases():
    """K6: Eine Scope-Exception aus einer FREMDEN Akte darf die Auflösung
    einer globalen DNA-Karte nicht überschreiben."""
    store, evo = make_evolution()
    # Globale Karte (case_id=None).
    global_card = store.add(MemoryCard(
        memory_type="governance", statement="Vanilla JS only",
        case_id=None, scope=("frontend",), valid_from="2026-01-01"))
    # Fremde Akte legt eine Karte an, die fälschlich auf die globale Kette zeigt.
    store.add(MemoryCard(
        memory_type="governance", statement="Vue erlaubt",
        case_id="fremde_akte", scope=("admin",), valid_from="2026-01-01",
        payload={"exception_to": global_card.card_id}))
    # Auflösung der globalen Karte mit scope 'admin' darf NICHT die fremde
    # Exception zurückgeben.
    resolved = evo.resolve(global_card.card_id, on_date="2026-06-15", scope="admin")
    assert resolved.card_id == global_card.card_id


def test_conflict_detection():
    store, evo = make_evolution()
    store.add(base_card(payload={"framework": "vanilla"}))
    store.add(base_card(statement="React verwenden", payload={"framework": "react"}))
    conflicts = evo.detect_conflicts(case_id="akte_1")
    assert len(conflicts) == 1
