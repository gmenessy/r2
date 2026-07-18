"""Billing: Key-Lifecycle, Token-Abrechnung, Budget-Durchsetzung, Reporting."""

from __future__ import annotations

import pytest

from apps.agent_layer.billing import BillingLedger, BudgetExceededError, PriceTable


@pytest.fixture()
def ledger() -> BillingLedger:
    return BillingLedger()


def test_key_lifecycle(ledger: BillingLedger) -> None:
    key = ledger.create_key("acme", budget_usd=1.0)
    assert key.startswith("agl_")
    assert ledger.resolve(key) == "acme"
    assert ledger.resolve("agl_wrong") is None
    assert ledger.revoke_key(key)
    assert ledger.resolve(key) is None
    assert not ledger.revoke_key("agl_wrong")


def test_llm_and_tool_charges_accumulate(ledger: BillingLedger) -> None:
    ledger.create_key("acme", budget_usd=5.0)
    cost = ledger.charge_llm("acme", "run_1", prompt_tokens=1_000_000, completion_tokens=500_000)
    assert cost == 150_000 + 300_000  # 0.15 + 0.30 USD in Mikro-USD
    ledger.charge_tool("acme", "run_1", "calc")

    usage = ledger.usage("acme")
    assert usage["by_kind"]["llm"]["units_in"] == 1_000_000
    assert usage["by_kind"]["tool"]["calls"] == 1
    assert usage["spent_usd"] == pytest.approx(0.4501)
    assert usage["remaining_usd"] == pytest.approx(5.0 - 0.4501)


def test_budget_enforcement_blocks_overspend(ledger: BillingLedger) -> None:
    ledger.create_key("tiny", budget_usd=0.0001)
    with pytest.raises(BudgetExceededError):
        ledger.charge_llm("tiny", "run_1", prompt_tokens=10_000_000, completion_tokens=0)
    # Fehlgeschlagene Buchung darf nichts verbuchen.
    assert ledger.usage("tiny")["spent_usd"] == 0.0


def test_unknown_tenant_has_no_budget_and_charges_freely(ledger: BillingLedger) -> None:
    ledger.charge_tool("ghost", "run_x", "calc")
    usage = ledger.usage("ghost")
    assert usage["budget_usd"] is None and usage["remaining_usd"] is None
    assert usage["spent_usd"] > 0


def test_run_cost_breakdown(ledger: BillingLedger) -> None:
    ledger.create_key("acme", budget_usd=5.0)
    ledger.charge_llm("acme", "run_9", 1000, 1000)
    ledger.charge_tool("acme", "run_9", "calc")
    report = ledger.run_cost("run_9")
    assert [item["kind"] for item in report["items"]] == ["llm", "tool"]
    assert report["items"][1]["detail"] == "calc"
    assert report["total_usd"] == pytest.approx(sum(i["cost_usd"] for i in report["items"]))


def test_tiny_calls_are_never_free(ledger: BillingLedger) -> None:
    """F8: Ceiling-Rundung — auch Mini-Calls kosten mindestens 1 Mikro-USD."""
    assert ledger.charge_llm("t", "r", prompt_tokens=1, completion_tokens=0) == 1
    assert ledger.prices.llm_cost(0, 0) == 0  # aber 0 Tokens kosten 0


def test_has_budget_preflight(ledger: BillingLedger) -> None:
    assert ledger.has_budget("unbudgetiert")  # ohne Budget: erlaubt
    ledger.create_key("acme", budget_usd=0.0002)  # 200 Mikro-USD = 2 Tool-Calls
    assert ledger.has_budget("acme")
    ledger.charge_tool("acme", "r", "calc")
    ledger.charge_tool("acme", "r", "calc")  # Budget exakt erschöpft
    assert not ledger.has_budget("acme")


def test_custom_price_table() -> None:
    prices = PriceTable(prompt_per_1m=1_000_000, completion_per_1m=2_000_000, per_tool_call=0)
    ledger = BillingLedger(prices=prices)
    assert ledger.charge_llm("t", "r", 500_000, 250_000) == 500_000 + 500_000
    assert ledger.charge_tool("t", "r", "free") == 0
