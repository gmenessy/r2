"""Sprint 5: Evaluation Harness — Memory-Qualität messbar machen."""

from brainfump.evaluation import (
    EvaluationHarness,
    GoldenScenario,
    case_scope_leakage_rate,
    governance_false_positive_rate,
    memory_precision_at_k,
    repeated_failure_avoidance,
    rule_compliance_rate,
)
from brainfump.gatekeeper import GateDecision, GateMode, MemoryGatekeeper
from brainfump.memory_cards import MemoryCard, MemoryCardStore


def test_memory_precision_at_k():
    assert memory_precision_at_k(["a", "b", "c"], {"a", "c"}, k=2) == 0.5
    assert memory_precision_at_k(["a", "b", "c"], {"a", "b"}, k=2) == 1.0
    assert memory_precision_at_k([], {"a"}, k=5) == 0.0


def test_rule_compliance_rate():
    assert rule_compliance_rate([True, True, False, True]) == 0.75
    assert rule_compliance_rate([]) == 1.0


def test_repeated_failure_avoidance():
    decisions = [
        GateDecision(mode=GateMode.BLOCK),
        GateDecision(mode=GateMode.ALLOW),
        GateDecision(mode=GateMode.SUGGEST_ALTERNATIVE),
        GateDecision(mode=GateMode.WARN),
    ]
    assert repeated_failure_avoidance(decisions) == 0.5


def test_governance_false_positive_rate():
    labeled = [
        (GateDecision(mode=GateMode.BLOCK), True),   # korrekt blockiert
        (GateDecision(mode=GateMode.BLOCK), False),  # unnötig blockiert
        (GateDecision(mode=GateMode.ALLOW), False),  # kein Eingriff
    ]
    assert governance_false_positive_rate(labeled) == 0.5


def test_case_scope_leakage_rate():
    store = MemoryCardStore()
    own = store.add(MemoryCard(memory_type="failure", statement="eigener", case_id="akte_1"))
    foreign = store.add(MemoryCard(memory_type="failure", statement="fremd", case_id="akte_2"))
    decisions = [
        GateDecision(mode=GateMode.BLOCK, consulted_memories=(own.card_id, foreign.card_id))
    ]
    assert case_scope_leakage_rate(decisions, "akte_1", store) == 0.5


def make_gatekeeper_with_history():
    store = MemoryCardStore()
    store.add(
        MemoryCard(
            memory_type="failure",
            statement="Retry-Fix scheiterte bereits.",
            case_id="akte_1",
            payload={"error_signature": "sig_timeout"},
        )
    )
    store.add(
        MemoryCard(
            memory_type="governance",
            statement="Produktionsdaten nicht löschen.",
            case_id=None,
            payload={"forbidden_actions": ["delete_production_data"]},
        )
    )
    return store, MemoryGatekeeper(store)


def test_harness_runs_golden_scenarios():
    store, gate = make_gatekeeper_with_history()
    scenarios = [
        GoldenScenario(
            name="repeat_failed_fix_blocked",
            action={"case_id": "akte_1", "error_signature": "sig_timeout"},
            expected_mode=GateMode.BLOCK,
            harmful=True,
        ),
        GoldenScenario(
            name="harmless_action_allowed",
            action={"case_id": "akte_1", "action_type": "format_code"},
            expected_mode=GateMode.ALLOW,
        ),
        GoldenScenario(
            name="governance_blocks_globally",
            action={"case_id": "akte_1", "action_type": "delete_production_data"},
            expected_mode=GateMode.BLOCK,
            harmful=True,
        ),
    ]
    report = EvaluationHarness(gate, store).run(scenarios)
    assert report.passed == 3
    assert report.failed == 0
    assert report.metrics["pass_rate"] == 1.0
    assert report.metrics["repeated_failure_avoidance"] == 1.0
    assert report.metrics["governance_false_positive_rate"] == 0.0
    assert report.metrics["memory_freshness_score"] > 0.9


def test_harness_reports_regressions():
    store, gate = make_gatekeeper_with_history()
    scenarios = [
        GoldenScenario(
            name="should_block_but_does_not",
            action={"case_id": "akte_1", "error_signature": "unbekannte_signatur"},
            expected_mode=GateMode.BLOCK,
            harmful=True,
        )
    ]
    report = EvaluationHarness(gate, store).run(scenarios)
    assert report.failed == 1
    assert report.failures[0]["expected"] == "block"
    assert report.failures[0]["actual"] == "allow"
