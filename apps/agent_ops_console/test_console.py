"""Ops Console: drei Szenarien end-to-end, Fach-Tools, Seeding, HTTP-Roundtrip."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from brainfump import BrainFumpKernel
from apps.agent_layer.billing import BillingLedger
from apps.agent_layer.ratelimit import RateLimiter
from apps.agent_layer.xai import TraceStore
from apps.agent_ops_console.console import (
    TENANT,
    OpsConsole,
    check_health,
    page_oncall,
    restart_service,
    scale_up,
)
from apps.agent_ops_console.server import create_server


@pytest.fixture()
def console() -> OpsConsole:
    return OpsConsole(BrainFumpKernel(None), BillingLedger(), TraceStore())


def _wait_done(console: OpsConsole, run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = console.run_status(run_id)
        if state and state["status"] == "done":
            return state
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not finish: {console.run_status(run_id)}")


def _tool_steps(console: OpsConsole, run_id: str) -> list[dict]:
    return [s["payload"] for s in console.traces.trace(run_id)["steps"]
            if s["kind"] == "tool_call"]


# -- Fach-Tools ----------------------------------------------------------------

def test_check_health_known_and_unknown_services() -> None:
    assert check_health("checkout-api") == {"service": "checkout-api", "status": "degraded",
                                             "latency_ms": 420}
    unknown = check_health("some-random-service-xyz")
    assert unknown["status"] in ("healthy", "degraded", "down")
    assert unknown["service"] == "some-random-service-xyz"
    # Deterministisch: derselbe Name liefert immer denselben Zustand.
    assert check_health("some-random-service-xyz") == unknown


def test_scale_up_and_restart_and_page() -> None:
    assert scale_up("checkout-api", 4) == {"service": "checkout-api", "replicas": 4,
                                            "scaled": True}
    assert restart_service("billing-service") == {"service": "billing-service",
                                                    "restarted": True}
    paged = page_oncall("billing-service", "hilfe")
    assert paged["paged"] is True and paged["channel"] == "pagerduty"


# -- Szenarien -------------------------------------------------------------------

def test_live_triage_runs_async_and_completes(console: OpsConsole) -> None:
    submitted = console.run_scenario("live_triage")
    assert submitted["async"] is True and submitted["status"] == "queued"
    assert submitted["run_id"].startswith("run_")

    state = _wait_done(console, submitted["run_id"])
    assert state["result"]["status"] == "ok"
    steps = _tool_steps(console, submitted["run_id"])
    assert [s["tool"] for s in steps] == ["check_health", "scale_up"]
    assert all(s["sandbox"]["exit_reason"] == "ok" for s in steps if "sandbox" in s)


def test_change_freeze_blocks_restart_but_pages_oncall(console: OpsConsole) -> None:
    result = console.run_scenario("change_freeze")
    assert result["async"] is False and result["status"] == "ok"
    steps = _tool_steps(console, result["run_id"])
    assert steps[0]["tool"] == "restart_service"
    assert steps[0]["gate"]["allowed"] is False
    assert "sandbox" not in steps[0]  # nie ausgeführt
    assert steps[1]["tool"] == "page_oncall"
    assert steps[1]["outcome"]["ok"] is True
    assert "FEHLGESCHLAGEN" in result["answer"] and "paged" in result["answer"]


def test_noisy_neighbor_hits_rate_limit_deterministically() -> None:
    # Injizierter Limiter mit fester Uhr statt Wanduhr — deterministisch.
    class Clock:
        t = 1000.0

        def __call__(self) -> float:
            return self.t

    limiter = RateLimiter(per_minute=60, burst=1, now=Clock())
    console = OpsConsole(BrainFumpKernel(None), BillingLedger(), TraceStore(),
                         rate_limiter=limiter)
    first = console.run_scenario("noisy_neighbor")
    assert first["status"] == "ok"
    second = console.run_scenario("noisy_neighbor")
    assert second["status"] == "rate_limited" and second["retry_after_s"] > 0
    assert second["title"] and second["pillar"]


def test_unknown_scenario_raises(console: OpsConsole) -> None:
    with pytest.raises(ValueError, match="unknown scenario"):
        console.run_scenario("gibts_nicht")


def test_run_status_of_unknown_run_is_none(console: OpsConsole) -> None:
    assert console.run_status("run_missing") is None


# -- Plattform-Zustand ------------------------------------------------------------

def test_seed_is_idempotent() -> None:
    kernel, ledger, traces = BrainFumpKernel(None), BillingLedger(), TraceStore()
    OpsConsole(kernel, ledger, traces)
    OpsConsole(kernel, ledger, traces)  # zweiter Start (z. B. Container-Restart)
    governance = [e for e in kernel.events.query(event_type="policy_violation")
                  if e.payload.get("forbidden_actions") == ["restart_service"]]
    assert len(governance) == 1
    assert ledger.usage(TENANT)["budget_usd"] == 5.0


def test_state_reports_llm_rate_limit_and_recent_runs(console: OpsConsole) -> None:
    console.run_scenario("change_freeze")
    state = console.state()
    assert state["llm"]["simulated"] is True
    assert state["sandbox_hardened"] is True
    assert state["rate_limit"] == {"per_minute": 12.0, "burst": 3.0} \
        or state["rate_limit"]["per_minute"] == 12
    assert state["tenant"]["tenant"] == TENANT
    assert state["recent_runs"][0]["status"] == "ok"


# -- HTTP-Roundtrip ------------------------------------------------------------

def _call(url: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def _status_of(call) -> int:
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        call()
    return excinfo.value.code


def test_http_roundtrip_scenarios_async_and_stream() -> None:
    console = OpsConsole(BrainFumpKernel(None), BillingLedger(), TraceStore())
    server = create_server(console, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        names = [s["name"] for s in _call(f"{base}/api/scenarios")["scenarios"]]
        assert names == ["live_triage", "change_freeze", "noisy_neighbor"]

        submitted = _call(f"{base}/api/scenarios/run", {"name": "live_triage"})
        assert submitted["status"] == "queued"
        run_id = submitted["run_id"]

        # Polling über /api/runs bis fertig.
        result = None
        for _ in range(200):
            state = _call(f"{base}/api/runs?run_id={run_id}")
            if state["status"] == "done":
                result = state["result"]
                break
            time.sleep(0.01)
        assert result is not None and result["status"] == "ok"

        trace = _call(f"{base}/api/trace?run_id={run_id}")
        assert any(s["kind"] == "tool_call" for s in trace["steps"])

        explanation = _call(f"{base}/api/explain?run_id={run_id}")
        assert explanation["cost"]["total_usd"] >= 0

        # Health/version aus dem Webkit-Baukasten.
        health = _call(f"{base}/api/health")
        assert health == {"status": "ok", "service": "agent-ops-console"}

        page = urllib.request.urlopen(f"{base}/").read().decode()
        assert "Agent Ops Console" in page
    finally:
        server.shutdown()


def test_sse_stream_over_http_emits_steps_and_done() -> None:
    console = OpsConsole(BrainFumpKernel(None), BillingLedger(), TraceStore())
    server = create_server(console, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        run = _call(f"{base}/api/scenarios/run", {"name": "change_freeze"})
        run_id = run["run_id"]
        request = urllib.request.Request(f"{base}/api/runs/stream?run_id={run_id}")
        events = []
        with urllib.request.urlopen(request, timeout=10) as response:
            assert response.headers["Content-Type"] == "text/event-stream"
            for raw in response:
                line = raw.decode().strip()
                if line.startswith("event:"):
                    events.append(line.split(":", 1)[1].strip())
                if events and events[-1] == "done":
                    break
        assert "step" in events and events[-1] == "done"
    finally:
        server.shutdown()


def test_rate_limit_returns_429_with_retry_after_over_http() -> None:
    class Clock:
        t = 2000.0

        def __call__(self) -> float:
            return self.t

    limiter = RateLimiter(per_minute=60, burst=1, now=Clock())
    console = OpsConsole(BrainFumpKernel(None), BillingLedger(), TraceStore(),
                         rate_limiter=limiter)
    server = create_server(console, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        assert _call(f"{base}/api/scenarios/run", {"name": "noisy_neighbor"})["status"] == "ok"
        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _call(f"{base}/api/scenarios/run", {"name": "noisy_neighbor"})
        assert excinfo.value.code == 429
        assert int(excinfo.value.headers["Retry-After"]) >= 1
        body = json.loads(excinfo.value.read())
        assert body["status"] == "rate_limited"
    finally:
        server.shutdown()


def test_validation_and_missing_resources() -> None:
    console = OpsConsole(BrainFumpKernel(None), BillingLedger(), TraceStore())
    server = create_server(console, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        assert _status_of(lambda: _call(f"{base}/api/scenarios/run",
                                        {"name": "gibts_nicht"})) == 400
        assert _status_of(lambda: _call(f"{base}/api/runs")) == 400
        assert _status_of(lambda: _call(f"{base}/api/runs?run_id=run_missing")) == 404
        assert _status_of(lambda: _call(f"{base}/api/runs/stream")) == 400
        assert _status_of(lambda: _call(f"{base}/api/runs/stream?run_id=run_missing")) == 404
        assert _status_of(lambda: _call(f"{base}/api/trace")) == 400
        assert _status_of(lambda: _call(f"{base}/api/trace?run_id=run_missing")) == 404
        assert _status_of(lambda: _call(f"{base}/api/explain?run_id=run_missing")) == 404
    finally:
        server.shutdown()
