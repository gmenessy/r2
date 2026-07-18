"""Flightdeck: vier Szenarien end-to-end, Fach-Tools, Seeding, HTTP-Roundtrip."""

from __future__ import annotations

import json
import threading
import urllib.request

import pytest

from brainfump import BrainFumpKernel
from apps.agent_flightdeck.flightdeck import (
    BROKE_TENANT,
    DEMO_TENANT,
    Flightdeck,
    parse_expense,
    policy_check,
)
from apps.agent_flightdeck.server import create_server
from apps.agent_layer.billing import BillingLedger
from apps.agent_layer.xai import TraceStore


@pytest.fixture()
def deck() -> Flightdeck:
    return Flightdeck(BrainFumpKernel(None), BillingLedger(), TraceStore())


def _tool_steps(deck: Flightdeck, run_id: str) -> list[dict]:
    return [s["payload"] for s in deck.traces.trace(run_id)["steps"]
            if s["kind"] == "tool_call"]


# -- Fach-Tools ----------------------------------------------------------------

def test_parse_expense_extracts_amount_category_receipt() -> None:
    parsed = parse_expense("Taxi Flughafen 62,50 EUR, Beleg fehlt")
    assert parsed == {"amount": 62.5, "category": "taxi", "has_receipt": False}
    assert parse_expense("Hotel 120 EUR, Beleg anbei")["has_receipt"] is True
    assert parse_expense("Kaffee ohne Betrag")["amount"] is None


def test_policy_check_rules() -> None:
    assert policy_check(62.5, "taxi", has_receipt=False)["verdict"] == "abgelehnt"
    assert policy_check(62.5, "taxi", has_receipt=True)["verdict"] == "genehmigt"
    assert policy_check(800.0, "hotel")["verdict"] == "review"


# -- Szenarien -----------------------------------------------------------------

def test_happy_path_scenario_runs_both_tools_sandboxed(deck: Flightdeck) -> None:
    result = deck.run_scenario("happy_path")
    assert result["status"] == "ok" and result["tool_calls"] == 2
    steps = _tool_steps(deck, result["run_id"])
    assert [s["tool"] for s in steps] == ["parse_expense", "policy_check"]
    assert all(s["sandbox"]["exit_reason"] == "ok" for s in steps)
    assert steps[1]["outcome"]["value"]["verdict"] == "abgelehnt"
    assert "abgelehnt" in result["answer"]
    assert result["cost_usd"] > 0


def test_gatekeeper_scenario_blocks_pay_out(deck: Flightdeck) -> None:
    result = deck.run_scenario("gatekeeper")
    assert result["status"] == "ok"
    step = _tool_steps(deck, result["run_id"])[0]
    assert step["tool"] == "pay_out"
    assert step["gate"]["allowed"] is False
    assert step["outcome"]["error"] == "blocked by memory gatekeeper"
    assert "sandbox" not in step  # nie ausgeführt
    assert "FEHLGESCHLAGEN" in result["answer"]


def test_sandbox_scenario_hits_wall_timeout(deck: Flightdeck) -> None:
    result = deck.run_scenario("sandbox")
    step = _tool_steps(deck, result["run_id"])[0]
    assert step["tool"] == "slow_scan"
    assert step["sandbox"]["exit_reason"] == "timeout"
    assert "wall timeout" in step["outcome"]["error"]
    assert result["status"] == "ok"  # der Run überlebt das Tool-Versagen


def test_budget_scenario_stops_deterministically(deck: Flightdeck) -> None:
    result = deck.run_scenario("budget")
    assert result["status"] == "budget_exceeded"
    assert deck.ledger.usage(BROKE_TENANT)["spent_usd"] == 0.0


def test_unknown_scenario_raises(deck: Flightdeck) -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        deck.run_scenario("gibts_nicht")


# -- Plattform-Zustand ---------------------------------------------------------

def test_seed_is_idempotent() -> None:
    kernel, ledger, traces = BrainFumpKernel(None), BillingLedger(), TraceStore()
    Flightdeck(kernel, ledger, traces)
    Flightdeck(kernel, ledger, traces)  # zweiter Start (z. B. Container-Restart)
    governance = [e for e in kernel.events.query(event_type="policy_violation")
                  if e.payload.get("forbidden_actions") == ["pay_out"]]
    assert len(governance) == 1
    assert ledger.usage(DEMO_TENANT)["budget_usd"] == 2.0


def test_state_reports_sim_mode_tenants_and_recent_runs(deck: Flightdeck) -> None:
    deck.run_goal('Rechne. [tool:calc {"expression": "2+2"}]')
    state = deck.state()
    assert state["llm"]["simulated"] is True
    assert state["sandbox_hardened"] is True
    assert {t["tenant"] for t in state["tenants"]} == {DEMO_TENANT, BROKE_TENANT}
    assert state["recent_runs"][0]["status"] == "ok"


def test_free_goal_runs_as_demo_tenant(deck: Flightdeck) -> None:
    result = deck.run_goal("Rechne (17+25)*3.")
    assert result["status"] == "ok"
    assert '"result": 126' in result["answer"]
    assert deck.ledger.usage(DEMO_TENANT)["spent_usd"] > 0


# -- HTTP-Roundtrip ------------------------------------------------------------

def test_http_roundtrip() -> None:
    deck = Flightdeck(BrainFumpKernel(None), BillingLedger(), TraceStore())
    server = create_server(deck, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def call(path: str, payload: dict | None = None) -> dict:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(f"{base}{path}", data=data,
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request) as response:
            return json.loads(response.read())

    try:
        names = [s["name"] for s in call("/api/scenarios")["scenarios"]]
        assert names == ["happy_path", "gatekeeper", "sandbox", "budget"]

        run = call("/api/scenarios/run", {"name": "happy_path"})
        assert run["status"] == "ok"

        trace = call(f"/api/trace?run_id={run['run_id']}")
        assert any(s["kind"] == "tool_call" for s in trace["steps"])

        explanation = call(f"/api/explain?run_id={run['run_id']}")
        assert explanation["cost"]["total_usd"] == pytest.approx(run["cost_usd"])

        state = call("/api/state")
        assert state["recent_runs"]

        tools = call("/api/tools")
        assert "parse_expense" in [t["name"] for t in tools["tools"]]

        page = urllib.request.urlopen(f"{base}/").read().decode()
        assert "Agent Flightdeck" in page
    finally:
        server.shutdown()
