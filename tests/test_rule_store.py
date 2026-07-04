"""E-1 — persistente, versionierte Rules Engine."""

from brainfump import BrainFumpKernel, TrustPolicy
from brainfump.gatekeeper import GateMode
from brainfump.rules import Rule, RuleStore


def make_rule(rid="no_react_abc", react=True):
    return Rule(
        rule_id=rid,
        condition={"case_id": "a"},
        check={"package_json_must_not_contain": ["react"]},
        severity="warning",
        message="keine react",
    )


# -- RuleStore (Einheit) -----------------------------------------------------

def test_add_active_and_roundtrip():
    store = RuleStore()
    store.add(make_rule())
    active = store.active()
    assert len(active) == 1
    assert active[0].rule_id == "no_react_abc"
    assert active[0].check == {"package_json_must_not_contain": ["react"]}


def test_add_is_idempotent_and_does_not_resurrect_revoked():
    store = RuleStore()
    store.add(make_rule())
    assert store.revoke("no_react_abc") is True
    assert store.status_of("no_react_abc") == "revoked"
    # Erneutes add (z. B. Backfill) darf die revoked Regel NICHT reaktivieren.
    store.add(make_rule())
    assert store.status_of("no_react_abc") == "revoked"
    assert store.active() == []


def test_revoke_only_affects_active():
    store = RuleStore()
    store.add(make_rule())
    assert store.revoke("no_react_abc") is True
    assert store.revoke("no_react_abc") is False  # schon revoked
    assert store.revoke("gibt_es_nicht") is False


def test_supersede_versions_the_rule():
    store = RuleStore()
    store.add(make_rule("rule_v1"))
    new = Rule(rule_id="rule_v2", condition={"case_id": "a"},
               check={"package_json_must_not_contain": ["vue"]})
    store.supersede("rule_v1", new)
    assert store.status_of("rule_v1") == "superseded"
    assert store.status_of("rule_v2") == "active"
    assert [r.rule_id for r in store.active()] == ["rule_v2"]


# -- Kernel-Integration ------------------------------------------------------

def _enforce(kernel):
    return kernel.check_action({
        "action_type": "finish_task", "case_id": "a",
        "context": {"case_id": "a", "package_json": {"dependencies": {"react": "^18"}}},
    }).mode


def test_rules_persist_across_restart_without_recompilation(tmp_path):
    path = str(tmp_path / "mem")
    k1 = BrainFumpKernel(path)
    k1.record("correction", "keine react-Abhängigkeit", case_id="a",
              payload={"forbid_dependencies": ["react"]})
    assert _enforce(k1) == GateMode.WARN
    assert len(k1.rule_store) == 1

    # Neustart: Regeln kommen aus dem Store.
    k2 = BrainFumpKernel(path)
    assert len(k2.checker.rules) == 1
    assert _enforce(k2) == GateMode.WARN


def test_revoke_rule_removes_enforcement_and_survives_restart(tmp_path):
    path = str(tmp_path / "mem")
    k1 = BrainFumpKernel(path)
    ev = k1.record("correction", "keine react-Abhängigkeit", case_id="a",
                   payload={"forbid_dependencies": ["react"]})
    rule_id = k1.checker.rules[0].rule_id
    assert _enforce(k1) == GateMode.WARN

    assert k1.revoke_rule(rule_id) is True
    assert _enforce(k1) == GateMode.ALLOW  # sofort entfernt

    # Nach Neustart bleibt die Regel zurückgenommen (Backfill belebt sie nicht).
    k2 = BrainFumpKernel(path)
    assert _enforce(k2) == GateMode.ALLOW
    # Das Event bleibt als Beweis erhalten (append-only).
    assert len(k2.events.query(event_type="correction")) == 1
    assert ev.event_id


def test_backfill_seeds_store_from_preexisting_corrections(tmp_path):
    """Bestandsdaten (Correction-Events ohne rules.db) werden beim Start
    einmalig in den Store übernommen."""
    path = str(tmp_path / "mem")
    k1 = BrainFumpKernel(path)
    k1.record("correction", "keine react-Abhängigkeit", case_id="a",
              payload={"forbid_dependencies": ["react"]})
    # rules.db löschen, um Bestandsdaten OHNE Regel-Store zu simulieren.
    import os
    os.remove(os.path.join(path, "rules.db"))

    k2 = BrainFumpKernel(path)
    assert len(k2.rule_store) == 1
    assert _enforce(k2) == GateMode.WARN


def test_untrusted_correction_creates_no_persistent_rule(tmp_path):
    path = str(tmp_path / "mem")
    kernel = BrainFumpKernel(path, trust=TrustPolicy(default=0.5).grant("bot", 0.1))
    kernel.record("correction", "keine react-Abhängigkeit", case_id="a", source="bot",
                  payload={"forbid_dependencies": ["react"]})
    assert len(kernel.rule_store) == 0
    assert _enforce(kernel) == GateMode.ALLOW
