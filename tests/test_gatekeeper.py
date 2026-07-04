"""Sprint 2: Memory Gatekeeper — Pre-Action Checks."""

from brainfump.gatekeeper import GateMode, MemoryGatekeeper
from brainfump.memory_cards import MemoryCard, MemoryCardStore
from brainfump.rules import Rule, RuntimeChecker


def make_store():
    return MemoryCardStore()


def test_allow_when_no_memories_match():
    gate = MemoryGatekeeper(make_store())
    decision = gate.check({"action_type": "apply_fix", "case_id": "akte_1"})
    assert decision.mode == GateMode.ALLOW
    assert decision.allowed


def test_blocks_repeat_of_failed_fix():
    store = make_store()
    store.add(
        MemoryCard(
            memory_type="failure",
            statement="Retry-Loop in payment.py hat das Problem nicht behoben.",
            case_id="akte_1",
            payload={"error_signature": "TimeoutError: payment gateway"},
        )
    )
    gate = MemoryGatekeeper(store)
    decision = gate.check(
        {
            "action_type": "apply_fix",
            "case_id": "akte_1",
            "error_signature": "TimeoutError: payment gateway",
        }
    )
    assert decision.mode == GateMode.BLOCK
    assert not decision.allowed
    assert decision.findings[0].gate == "no_repeat_failed_fix"
    assert decision.consulted_memories


def test_suggests_alternative_when_failure_card_has_one():
    store = make_store()
    store.add(
        MemoryCard(
            memory_type="failure",
            statement="Fix A scheiterte.",
            case_id="akte_1",
            payload={
                "error_signature": "sig_1",
                "alternative": "Stattdessen Circuit Breaker einbauen.",
            },
        )
    )
    gate = MemoryGatekeeper(store)
    decision = gate.check({"case_id": "akte_1", "error_signature": "sig_1"})
    assert decision.mode == GateMode.SUGGEST_ALTERNATIVE
    assert "Circuit Breaker" in decision.suggested_alternative


def test_fragile_file_requires_review():
    store = make_store()
    store.add(
        MemoryCard(
            memory_type="risk",
            statement="legacy_billing.py ist fragil, Änderungen brauchen Review.",
            case_id="akte_1",
            payload={"fragile_files": ["legacy_billing.py"]},
        )
    )
    gate = MemoryGatekeeper(store)
    decision = gate.check(
        {"action_type": "modify_file", "case_id": "akte_1", "files": ["legacy_billing.py"]}
    )
    assert decision.mode == GateMode.REQUIRE_REVIEW


def test_governance_card_blocks_forbidden_action():
    store = make_store()
    store.add(
        MemoryCard(
            memory_type="governance",
            statement="Produktionsdaten dürfen nie automatisch gelöscht werden.",
            case_id=None,  # globale DNA
            payload={"forbidden_actions": ["delete_production_data"]},
        )
    )
    gate = MemoryGatekeeper(store)
    decision = gate.check({"action_type": "delete_production_data", "case_id": "akte_1"})
    assert decision.mode == GateMode.BLOCK
    assert decision.findings[0].gate == "governance"


def test_case_boundary_no_leakage():
    """Failure aus einer anderen Akte darf diese Akte nicht beeinflussen."""
    store = make_store()
    store.add(
        MemoryCard(
            memory_type="failure",
            statement="Fremder Fehler",
            case_id="akte_2",
            payload={"error_signature": "sig_x"},
        )
    )
    gate = MemoryGatekeeper(store)
    decision = gate.check({"case_id": "akte_1", "error_signature": "sig_x"})
    assert decision.mode == GateMode.ALLOW
    assert not decision.consulted_memories


def test_expired_failure_card_does_not_fire():
    """M1: Eine per valid_to abgelaufene failure-Karte blockiert nicht mehr."""
    store = make_store()
    store.add(
        MemoryCard(
            memory_type="failure",
            statement="Alter, abgelaufener Fehlversuch.",
            case_id="akte_1",
            valid_from="2020-01-01",
            valid_to="2020-12-31",  # längst abgelaufen
            payload={"error_signature": "sig_alt"},
        )
    )
    gate = MemoryGatekeeper(store)
    decision = gate.check(
        {"action_type": "apply_fix", "case_id": "akte_1", "error_signature": "sig_alt"}
    )
    assert decision.mode == GateMode.ALLOW
    assert not decision.consulted_memories


def test_runtime_rule_violation_warns():
    checker = RuntimeChecker(
        [
            Rule(
                rule_id="no_react_for_mvp",
                condition={"project_type": "mvp_frontend"},
                check={"package_json_must_not_contain": ["react"]},
                severity="warning",
                message="Keine React-Abhängigkeit im MVP.",
            )
        ]
    )
    gate = MemoryGatekeeper(make_store(), checker)
    decision = gate.check(
        {
            "action_type": "finish_task",
            "case_id": "akte_1",
            "context": {
                "project_type": "mvp_frontend",
                "package_json": {"dependencies": {"react": "^18.0.0"}},
            },
        }
    )
    assert decision.mode == GateMode.WARN
    assert decision.findings[0].gate == "runtime_rule"


def test_signature_matching_is_normalized():
    """Matching-Härtung: triviale Signatur-Mutationen (Case, Whitespace)
    umgehen das Failure-Gate nicht mehr."""
    store = make_store()
    store.add(
        MemoryCard(
            memory_type="failure",
            statement="Retry scheiterte.",
            case_id="akte_1",
            payload={"error_signature": "TimeoutError: gateway"},
        )
    )
    gate = MemoryGatekeeper(store)
    for mutated in ("TimeoutError: gateway ", "  timeouterror:   GATEWAY", "TIMEOUTERROR: gateway"):
        d = gate.check({"action_type": "apply_fix", "case_id": "akte_1", "error_signature": mutated})
        assert d.mode == GateMode.BLOCK, mutated
    # Wirklich andere Signatur bleibt erlaubt (kein Overblocking).
    other = gate.check({"action_type": "apply_fix", "case_id": "akte_1",
                        "error_signature": "ValueError: parse"})
    assert other.mode == GateMode.ALLOW


def test_governance_pattern_blocks_renamed_action():
    """Matching-Härtung: ein forbidden_action_pattern fängt umbenannte,
    semantisch gleiche Aktionen."""
    store = make_store()
    store.add(
        MemoryCard(
            memory_type="governance",
            statement="Keine destruktiven Aktionen auf Produktionsdaten.",
            case_id=None,
            payload={"forbidden_action_patterns": [r"(delete|drop|purge).*(prod|production)"]},
        )
    )
    gate = MemoryGatekeeper(store)
    for act in ("delete_production_data", "drop_prod_tables", "purge_production_cache"):
        assert gate.check({"action_type": act, "case_id": "akte_1"}).mode == GateMode.BLOCK, act
    # Harmlose Aktion bleibt frei.
    assert gate.check({"action_type": "read_prod_config", "case_id": "akte_1"}).mode == GateMode.ALLOW


def test_highest_mode_wins():
    store = make_store()
    store.add(
        MemoryCard(
            memory_type="risk",
            statement="fragile Datei",
            case_id="akte_1",
            payload={"fragile_files": ["a.py"]},
        )
    )
    store.add(
        MemoryCard(
            memory_type="governance",
            statement="verboten",
            case_id="akte_1",
            payload={"forbidden_actions": ["rewrite_history"]},
        )
    )
    gate = MemoryGatekeeper(store)
    decision = gate.check(
        {"action_type": "rewrite_history", "case_id": "akte_1", "files": ["a.py"]}
    )
    assert decision.mode == GateMode.BLOCK
    assert len(decision.findings) == 2
