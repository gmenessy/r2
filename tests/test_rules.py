"""Sprint 4: Runtime Rule Compiler — Korrekturen werden ausführbar."""

from brainfump.events import Event
from brainfump.rules import Rule, RuleCompiler, RuntimeChecker


def test_compile_structured_correction():
    event = Event(
        event_type="correction",
        content="Bitte keine React-Abhängigkeit im MVP.",
        case_id="akte_1",
        payload={"forbid_dependencies": ["react"], "project_type": "mvp_frontend"},
    )
    rule = RuleCompiler().compile_correction(event)
    assert rule.check == {"package_json_must_not_contain": ["react"]}
    assert rule.condition == {"project_type": "mvp_frontend", "case_id": "akte_1"}
    assert rule.source == event.event_id


def test_compile_natural_language_correction():
    event = Event(
        event_type="correction",
        content="Bitte kein Vue und keine React-Abhängigkeit verwenden.",
    )
    rule = RuleCompiler().compile_correction(event)
    assert rule.check == {"package_json_must_not_contain": ["react", "vue"]}


def test_non_correction_events_ignored():
    event = Event(event_type="decision", content="Wir nehmen kein React.")
    assert RuleCompiler().compile_correction(event) is None


def test_correction_without_recognizable_target_ignored():
    event = Event(event_type="correction", content="Bitte freundlicher formulieren.")
    assert RuleCompiler().compile_correction(event) is None


def test_runtime_check_detects_violation():
    rule = Rule(
        rule_id="no_react",
        condition={"project_type": "mvp_frontend"},
        check={"package_json_must_not_contain": ["react"]},
        severity="warning",
    )
    checker = RuntimeChecker([rule])
    violations = checker.check(
        {
            "project_type": "mvp_frontend",
            "package_json": {"dependencies": {"react": "^18.0.0", "lodash": "^4"}},
        }
    )
    assert len(violations) == 1
    assert violations[0].details["forbidden_found"] == ["react"]


def test_condition_gates_the_check():
    rule = Rule(
        rule_id="no_react",
        condition={"project_type": "mvp_frontend"},
        check={"package_json_must_not_contain": ["react"]},
    )
    checker = RuntimeChecker([rule])
    # Anderer Projekttyp: Regel greift nicht (Scope-Bindung).
    assert checker.check(
        {"project_type": "admin_backend", "package_json": {"dependencies": {"react": "1"}}}
    ) == []


def test_devdependencies_also_checked():
    rule = Rule(rule_id="r", condition={}, check={"package_json_must_not_contain": ["react"]})
    violations = RuntimeChecker([rule]).check(
        {"package_json": {"devDependencies": {"react": "^18"}}}
    )
    assert len(violations) == 1


def test_must_contain_and_must_not_contain():
    rules = [
        Rule(
            rule_id="german_answers",
            condition={},
            check={"must_contain": {"field": "answer_language", "values": ["de"]}},
            severity="info",
        ),
        Rule(
            rule_id="no_todo",
            condition={},
            check={"must_not_contain": {"field": "diff", "values": ["TODO"]}},
            severity="warning",
        ),
    ]
    checker = RuntimeChecker(rules)
    violations = checker.check({"answer_language": "en", "diff": "x = 1  # TODO fix"})
    assert {v.rule_id for v in violations} == {"german_answers", "no_todo"}
