"""E-4 — Mandantenfähigkeit (Multi-Tenant-Isolation)."""

import pytest

from brainfump import BrainFumpKernel, TenantManager, TrustPolicy
from brainfump.gatekeeper import GateMode


def test_tenants_are_isolated_in_memory():
    tm = TenantManager()  # ephemer, :memory: pro Mandant
    tm.kernel_for("acme").record("failed_attempt", "Fix scheiterte", case_id="repo",
                                 payload={"error_signature": "boom"})
    # Anderer Mandant sieht davon nichts.
    other = tm.kernel_for("globex")
    assert other.check_action({"action_type": "apply_fix", "case_id": "repo",
                               "error_signature": "boom"}).mode == GateMode.ALLOW
    # Kein failed_attempt von acme im Log des anderen Mandanten.
    assert len(other.events.query(event_type="failed_attempt")) == 0
    assert len(other.cards.active(case_id="repo")) == 0


def test_kernel_is_cached_per_tenant():
    tm = TenantManager()
    assert tm.kernel_for("acme") is tm.kernel_for("acme")
    assert tm.kernel_for("acme") is not tm.kernel_for("globex")


def test_tenants_listing_and_persistence(tmp_path):
    tm = TenantManager(str(tmp_path))
    tm.kernel_for("acme").record("decision", "X", case_id="a")
    tm.kernel_for("globex").record("decision", "Y", case_id="b")
    assert tm.tenants() == ["acme", "globex"]

    # Neuer Manager auf demselben Root sieht die Mandanten und ihre Daten wieder.
    tm2 = TenantManager(str(tmp_path))
    assert tm2.tenants() == ["acme", "globex"]
    assert len(tm2.kernel_for("acme").events.query(case_id="a")) == 1
    assert len(tm2.kernel_for("acme").events.query(case_id="b")) == 0  # Isolation


def test_drop_removes_tenant_data(tmp_path):
    tm = TenantManager(str(tmp_path))
    tm.kernel_for("acme").record("decision", "X", case_id="a")
    assert tm.drop("acme") is True
    assert tm.tenants() == []
    # Danach ist der Mandant leer (frischer Kernel).
    assert len(tm.kernel_for("acme").events) == 0


def test_invalid_tenant_ids_rejected():
    tm = TenantManager()
    for bad in ("", "../etc", "a/b", ".", "..", "with space"):
        with pytest.raises(ValueError):
            tm.kernel_for(bad)


def test_custom_kernel_factory_applies_per_tenant():
    # Jeder Mandant bekommt dieselbe restriktive Trust-Policy.
    def factory(path):
        return BrainFumpKernel(path, trust=TrustPolicy(default=0.5).grant("bot", 0.1))

    tm = TenantManager(kernel_factory=factory)
    k = tm.kernel_for("acme")
    k.record("policy_violation", "verboten", case_id=None, source="bot",
             payload={"forbidden_actions": ["write_file"]})
    # Untrusted globale DNA wird nicht aktiv — Policy greift pro Mandant.
    assert k.check_action({"action_type": "write_file", "case_id": "x"}).mode == GateMode.ALLOW
