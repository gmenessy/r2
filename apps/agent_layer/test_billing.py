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


def test_reservation_blocks_overspend_before_the_call(ledger: BillingLedger) -> None:
    """S3-3/O4: reserve() bindet den Höchstpreis vorab und verhindert, dass ein
    Call, der das Budget sprengen würde, überhaupt ausgelöst wird."""
    ledger.create_key("acme", budget_usd=0.001)  # 1000 Mikro-USD
    res = ledger.reserve("acme", "run_1", amount_micro=900)
    assert res.startswith("res_")
    assert ledger.has_budget("acme")  # noch 100 Mikro-USD übrig
    # Zweite Reservierung über den Rest hinaus wird abgelehnt — vor jedem Call.
    with pytest.raises(BudgetExceededError, match="reservation"):
        ledger.reserve("acme", "run_1", amount_micro=200)


def test_settle_books_actual_and_credits_the_difference(ledger: BillingLedger) -> None:
    ledger.create_key("acme", budget_usd=1.0)
    res = ledger.reserve("acme", "run_1", amount_micro=600_000)  # Höchstpreis gebunden
    # Während der Reservierung ist das gebundene Budget nicht mehr verfügbar.
    assert ledger.usage("acme")["spent_usd"] == 0.0
    assert ledger._committed("acme") == 600_000
    cost = ledger.settle(res, "acme", "run_1", prompt_tokens=1000, completion_tokens=100)
    # Ist-Kosten: 1000*0.15 + 100*0.60 (pro 1M) = 210 Mikro-USD, Rest gutgeschrieben.
    assert cost == 210
    usage = ledger.usage("acme")
    assert usage["spent_usd"] == pytest.approx(210 / 1_000_000)
    assert usage["remaining_usd"] == pytest.approx(1.0 - 210 / 1_000_000)
    assert ledger._active_reservations("acme") == 0  # Reservierung aufgelöst


def test_release_frees_reservation_without_booking(ledger: BillingLedger) -> None:
    ledger.create_key("acme", budget_usd=1.0)
    res = ledger.reserve("acme", "run_1", amount_micro=600_000)
    ledger.release(res)
    assert ledger._active_reservations("acme") == 0
    assert ledger.usage("acme")["spent_usd"] == 0.0  # nichts verbucht
    # Volles Budget wieder reservierbar.
    assert ledger.reserve("acme", "run_2", amount_micro=900_000).startswith("res_")


def test_tool_charge_respects_open_reservations(ledger: BillingLedger) -> None:
    """Eine offene LLM-Reservierung schützt auch vor Tool-Buchungen darüber hinaus."""
    ledger.create_key("acme", budget_usd=0.0002)  # 200 Mikro-USD, Tool = 100 Mikro
    ledger.reserve("acme", "run_1", amount_micro=50)  # committed=50
    ledger.charge_tool("acme", "run_1", "calc")       # 50+100 = 150 <= 200 → ok
    with pytest.raises(BudgetExceededError):           # 150+100 = 250 > 200 → block
        ledger.charge_tool("acme", "run_1", "calc")


def test_key_expiry(ledger: BillingLedger) -> None:
    """S3-4: Ein Key mit TTL wird nach Ablauf nicht mehr aufgelöst."""
    import time as _time

    live = ledger.create_key("acme", budget_usd=1.0, ttl_seconds=3600)
    assert ledger.resolve(live) == "acme"
    expired = ledger.create_key("acme", budget_usd=1.0, ttl_seconds=-1)  # bereits abgelaufen
    assert ledger.resolve(expired) is None
    # Abgelaufener Key zählt auch nicht mehr zum Budget.
    ledger.revoke_key(live)
    fresh = ledger.create_key("beta", budget_usd=2.0, ttl_seconds=0.01)
    _time.sleep(0.02)
    assert ledger.resolve(fresh) is None
    assert ledger.usage("beta")["budget_usd"] is None


def test_key_rotation_grace_and_no_budget_doubling(ledger: BillingLedger) -> None:
    """S3-4: Rotation gibt einen neuen Key; der alte gilt im Kulanzfenster
    weiter, das Tenant-Budget verdoppelt sich dabei NICHT."""
    old = ledger.create_key("acme", budget_usd=5.0)
    assert ledger.usage("acme")["budget_usd"] == 5.0

    new = ledger.rotate_key(old, grace_seconds=300)
    assert new is not None and new != old
    # Beide Keys lösen auf (Kulanzfenster), Budget bleibt 5.0 (nicht 10.0).
    assert ledger.resolve(old) == "acme"
    assert ledger.resolve(new) == "acme"
    assert ledger.usage("acme")["budget_usd"] == 5.0

    # Rotation eines unbekannten Keys → None.
    assert ledger.rotate_key("agl_unbekannt") is None
    # Nach Widerruf ist Rotation nicht mehr möglich.
    ledger.revoke_key(new)
    assert ledger.rotate_key(new) is None


def test_custom_price_table() -> None:
    prices = PriceTable(prompt_per_1m=1_000_000, completion_per_1m=2_000_000, per_tool_call=0)
    ledger = BillingLedger(prices=prices)
    assert ledger.charge_llm("t", "r", 500_000, 250_000) == 500_000 + 500_000
    assert ledger.charge_tool("t", "r", "free") == 0
