"""HTTP-Roundtrip der Plattform-API: Auth, Run, Trace, Explain, Usage, Tools."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from brainfump import BrainFumpKernel
from apps.agent_layer.billing import BillingLedger
from apps.agent_layer.llm import ChatResult, ToolCall
from apps.agent_layer.runtime import AgentRuntime
from apps.agent_layer.server import create_server
from apps.agent_layer.tools import builtin_registry
from apps.agent_layer.xai import TraceStore

ADMIN_TOKEN = "test-admin"


class ScriptedLLM:
    def __init__(self) -> None:
        self.script = [
            ChatResult(content="", prompt_tokens=100, completion_tokens=10, tool_calls=[
                ToolCall(call_id="call_1", name="calc", arguments={"expression": "6*7"}),
            ]),
            ChatResult(content="Das Ergebnis ist 42.", prompt_tokens=150, completion_tokens=12),
        ]

    def chat(self, messages, tools=None, **_kwargs) -> ChatResult:
        return self.script.pop(0)


@pytest.fixture()
def api():
    kernel = BrainFumpKernel(None)
    ledger = BillingLedger()
    traces = TraceStore()
    runtime = AgentRuntime(llm=ScriptedLLM(), registry=builtin_registry(kernel),
                           traces=traces, kernel=kernel, ledger=ledger)
    server = create_server(runtime, ledger, traces, admin_token=ADMIN_TOKEN,
                           host="127.0.0.1", port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()


def _call(url: str, payload: dict | None = None, headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json",
                                              **(headers or {})})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def _status_of(call) -> int:
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        call()
    return excinfo.value.code


def test_full_platform_roundtrip(api: str) -> None:
    # 1. Admin stellt einen API-Key aus.
    issued = _call(f"{api}/api/keys", {"tenant": "acme", "budget_usd": 2.0},
                   headers={"X-Admin-Token": ADMIN_TOKEN})
    key = issued["api_key"]
    assert key.startswith("agl_") and issued["tenant"] == "acme"

    # 2. Run über die API — Tool-Call läuft durch die Sandbox.
    run = _call(f"{api}/api/run", {"goal": "Rechne 6*7", "case_id": "akte_1"},
                headers={"X-API-Key": key})
    assert run["status"] == "ok" and run["answer"] == "Das Ergebnis ist 42."
    assert run["tool_calls"] == 1 and run["cost_usd"] > 0

    # 3. xAI: Trace und Explain sind für den eigenen Tenant abrufbar.
    trace = _call(f"{api}/api/trace?run_id={run['run_id']}", headers={"X-API-Key": key})
    assert [s["kind"] for s in trace["steps"]] == ["memory_hits", "llm_call",
                                                   "tool_call", "llm_call"]
    explanation = _call(f"{api}/api/explain?run_id={run['run_id']}",
                        headers={"X-API-Key": key})
    assert explanation["llm"]["calls"] == 2
    assert explanation["cost"]["total_usd"] == pytest.approx(run["cost_usd"])

    # 4. Billing: Verbrauch des Tenants inkl. Budget.
    usage = _call(f"{api}/api/usage", headers={"X-API-Key": key})
    assert usage["tenant"] == "acme"
    assert usage["spent_usd"] == pytest.approx(run["cost_usd"])
    assert usage["remaining_usd"] == pytest.approx(2.0 - run["cost_usd"])


def test_auth_failures(api: str) -> None:
    assert _status_of(lambda: _call(f"{api}/api/keys", {"tenant": "x"},
                                    headers={"X-Admin-Token": "wrong"})) == 403
    assert _status_of(lambda: _call(f"{api}/api/run", {"goal": "hi"})) == 401
    assert _status_of(lambda: _call(f"{api}/api/run", {"goal": "hi"},
                                    headers={"X-API-Key": "agl_fake"})) == 401
    assert _status_of(lambda: _call(f"{api}/api/usage")) == 401


def test_validation_and_missing_resources(api: str) -> None:
    issued = _call(f"{api}/api/keys", {"tenant": "acme"},
                   headers={"X-Admin-Token": ADMIN_TOKEN})
    key = issued["api_key"]
    assert _status_of(lambda: _call(f"{api}/api/run", {},
                                    headers={"X-API-Key": key})) == 400
    assert _status_of(lambda: _call(f"{api}/api/trace",
                                    headers={"X-API-Key": key})) == 400
    assert _status_of(lambda: _call(f"{api}/api/trace?run_id=run_missing",
                                    headers={"X-API-Key": key})) == 404
    assert _status_of(lambda: _call(f"{api}/api/explain?run_id=run_missing",
                                    headers={"X-API-Key": key})) == 404


def test_trace_isolation_between_tenants(api: str) -> None:
    """F1: Traces sind mandantengetrennt — fremde Runs sind tabu, Admin darf."""
    acme = _call(f"{api}/api/keys", {"tenant": "acme"},
                 headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
    rival = _call(f"{api}/api/keys", {"tenant": "rival"},
                  headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
    run = _call(f"{api}/api/run", {"goal": "Rechne 6*7"}, headers={"X-API-Key": acme})

    # Ohne Key: 401. Fremder Tenant: 403. Eigener Tenant und Admin: 200.
    assert _status_of(lambda: _call(f"{api}/api/trace?run_id={run['run_id']}")) == 401
    assert _status_of(lambda: _call(f"{api}/api/trace?run_id={run['run_id']}",
                                    headers={"X-API-Key": rival})) == 403
    assert _status_of(lambda: _call(f"{api}/api/explain?run_id={run['run_id']}",
                                    headers={"X-API-Key": rival})) == 403
    own = _call(f"{api}/api/trace?run_id={run['run_id']}", headers={"X-API-Key": acme})
    assert own["tenant"] == "acme"
    admin = _call(f"{api}/api/trace?run_id={run['run_id']}",
                  headers={"X-Admin-Token": ADMIN_TOKEN})
    assert admin["run_id"] == run["run_id"]


def test_tools_and_health_endpoints(api: str) -> None:
    tools = _call(f"{api}/api/tools")
    names = [t["name"] for t in tools["tools"]]
    assert "calc" in names and "memory_search" in names
    assert tools["sandbox_hardened"] is True

    health = _call(f"{api}/api/health")
    assert health == {"status": "ok", "service": "agent-layer"}
