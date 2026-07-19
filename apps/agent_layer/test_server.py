"""HTTP-Roundtrip der Plattform-API: Auth, Run, Trace, Explain, Usage, Tools."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

import pytest

from brainfump import BrainFumpKernel
from apps.agent_layer.billing import BillingLedger
from apps.agent_layer.llm import ChatResult, ToolCall
from apps.agent_layer.ratelimit import RateLimiter
from apps.agent_layer.runner import AsyncRunner
from apps.agent_layer.runtime import AgentRuntime
from apps.agent_layer.server import create_server
from apps.agent_layer.sharding import ShardManifest
from apps.agent_layer.simllm import SimulatedLLM
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


def _error_of(call):
    """Gibt (status, body, headers) einer erwarteten HTTP-Fehlerantwort zurück."""
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        call()
    err = excinfo.value
    return err.code, json.loads(err.read()), err.headers


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


def test_rate_limit_returns_429_with_retry_after() -> None:
    """S3-4/O5: Über dem Limit antwortet /api/run mit 429 + Retry-After."""
    kernel = BrainFumpKernel(None)
    ledger = BillingLedger()
    traces = TraceStore()
    runtime = AgentRuntime(llm=SimulatedLLM(), registry=builtin_registry(kernel),
                           traces=traces, kernel=kernel, ledger=ledger)
    limiter = RateLimiter(per_minute=60, burst=2)  # zwei Runs frei, dann Sperre
    server = create_server(runtime, ledger, traces, admin_token=ADMIN_TOKEN,
                           host="127.0.0.1", port=0, rate_limiter=limiter)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        key = _call(f"{base}/api/keys", {"tenant": "acme", "budget_usd": 5.0},
                    headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
        assert _call(f"{base}/api/run", {"goal": "hi"}, headers={"X-API-Key": key})["status"]
        assert _call(f"{base}/api/run", {"goal": "hi"}, headers={"X-API-Key": key})["status"]
        status, body, headers = _error_of(
            lambda: _call(f"{base}/api/run", {"goal": "hi"}, headers={"X-API-Key": key}))
        assert status == 429
        assert body["error"] == "rate limit exceeded" and body["retry_after_s"] > 0
        assert int(headers["Retry-After"]) >= 1
    finally:
        server.shutdown()


def test_key_rotation_endpoint(api: str) -> None:
    """S3-4: /api/keys/rotate liefert einen neuen Key; der alte gilt weiter (Grace)."""
    old = _call(f"{api}/api/keys", {"tenant": "acme", "budget_usd": 2.0},
                headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
    rotated = _call(f"{api}/api/keys/rotate", {"grace_seconds": 120},
                    headers={"X-API-Key": old})
    new = rotated["api_key"]
    assert new.startswith("agl_") and new != old
    # Neuer Key funktioniert für /api/usage; Budget bleibt bei 2.0 (keine Verdopplung).
    usage = _call(f"{api}/api/usage", headers={"X-API-Key": new})
    assert usage["tenant"] == "acme" and usage["budget_usd"] == 2.0
    # Rotation ohne gültigen Key → 401.
    assert _status_of(lambda: _call(f"{api}/api/keys/rotate", {},
                                    headers={"X-API-Key": "agl_fake"})) == 401


def _post_expect(url: str, payload: dict, headers: dict):
    """POST, das eine Nicht-2xx-Antwort erwartet → (status, body)."""
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def test_async_run_submit_poll_and_isolation() -> None:
    """S4-2: async Run liefert sofort 202+run_id; /api/runs pollt bis done."""
    kernel = BrainFumpKernel(None)
    ledger = BillingLedger()
    traces = TraceStore()
    runtime = AgentRuntime(llm=SimulatedLLM(), registry=builtin_registry(kernel),
                           traces=traces, kernel=kernel, ledger=ledger)
    runner = AsyncRunner(runtime, max_workers=2)
    server = create_server(runtime, ledger, traces, admin_token=ADMIN_TOKEN,
                           host="127.0.0.1", port=0, async_runner=runner)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        key = _call(f"{base}/api/keys", {"tenant": "acme", "budget_usd": 5.0},
                    headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
        rival = _call(f"{base}/api/keys", {"tenant": "rival", "budget_usd": 5.0},
                      headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]

        status, submitted = _post_expect(
            f"{base}/api/run", {"goal": "Rechne (6*7).", "async": True},
            headers={"X-API-Key": key})
        assert status == 202 and submitted["status"] == "queued"
        run_id = submitted["run_id"]

        # Fremder Tenant darf den Status nicht sehen.
        assert _status_of(lambda: _call(f"{base}/api/runs?run_id={run_id}",
                                        headers={"X-API-Key": rival})) == 403

        # Eigener Tenant pollt bis done.
        result = None
        for _ in range(200):
            state = _call(f"{base}/api/runs?run_id={run_id}", headers={"X-API-Key": key})
            if state["status"] == "done":
                result = state["result"]
                break
            time.sleep(0.01)
        assert result is not None and result["status"] == "ok"
        assert "42" in result["answer"]
    finally:
        server.shutdown()
        runner.shutdown()


def test_async_global_backpressure_returns_503() -> None:
    """S4-5: bei voller globaler Queue antwortet /api/run mit 503 + Retry-After."""
    import threading as _t

    class BlockingLLM:
        model = "b"
        base_url = "t://b"

        def __init__(self) -> None:
            self.release = _t.Event()

        def chat(self, messages, tools=None, **_):
            self.release.wait(timeout=5)
            return SimulatedLLM().chat(messages, tools=tools)

    kernel = BrainFumpKernel(None)
    ledger = BillingLedger()
    traces = TraceStore()
    llm = BlockingLLM()
    runtime = AgentRuntime(llm=llm, registry=builtin_registry(kernel), traces=traces,
                           kernel=kernel, ledger=ledger)
    runner = AsyncRunner(runtime, max_workers=2, max_inflight_total=1,
                         max_inflight_per_tenant=10)
    server = create_server(runtime, ledger, traces, admin_token=ADMIN_TOKEN,
                           host="127.0.0.1", port=0, async_runner=runner)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        key = _call(f"{base}/api/keys", {"tenant": "acme", "budget_usd": 50.0},
                    headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
        s1, _ = _post_expect(f"{base}/api/run", {"goal": "a", "async": True},
                             headers={"X-API-Key": key})
        assert s1 == 202
        s2, body = _post_expect(f"{base}/api/run", {"goal": "b", "async": True},
                                headers={"X-API-Key": key})
        assert s2 == 503 and body["error"] == "run queue full" and body["retry_after_s"] > 0
    finally:
        llm.release.set()
        server.shutdown()
        runner.shutdown()


def test_sse_stream_emits_steps_and_done(api: str) -> None:
    """S4-3: /api/runs/stream liefert die Trace-Schritte als SSE + done-Event."""
    key = _call(f"{api}/api/keys", {"tenant": "acme", "budget_usd": 5.0},
                headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
    run = _call(f"{api}/api/run", {"goal": "Rechne 6*7"}, headers={"X-API-Key": key})
    run_id = run["run_id"]

    request = urllib.request.Request(f"{api}/api/runs/stream?run_id={run_id}",
                                     headers={"X-API-Key": key})
    events = []
    with urllib.request.urlopen(request, timeout=10) as response:
        assert response.headers["Content-Type"] == "text/event-stream"
        for raw in response:
            line = raw.decode().strip()
            if line.startswith("event:"):
                events.append(line.split(":", 1)[1].strip())
            if line.startswith("event: done") or "done" in events:
                if events and events[-1] == "done":
                    break
    assert "step" in events           # mindestens ein Trace-Schritt gestreamt
    assert events[-1] == "done"       # sauberer Abschluss

    # Fremder Tenant kommt nicht an den Stream.
    rival = _call(f"{api}/api/keys", {"tenant": "rival"},
                  headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
    assert _status_of(lambda: _call(f"{api}/api/runs/stream?run_id={run_id}",
                                    headers={"X-API-Key": rival})) == 403


def _shard_server(shard_index: int, total: int, port: int = 0):
    """Eine Instanz mit Sharding-Konfiguration; gibt (base_url, server) zurück."""
    kernel = BrainFumpKernel(None)
    ledger = BillingLedger()
    traces = TraceStore()
    runtime = AgentRuntime(llm=SimulatedLLM(), registry=builtin_registry(kernel),
                           traces=traces, kernel=kernel, ledger=ledger)
    manifest = ShardManifest(total, urls={i: f"http://shard-{i}:8060" for i in range(total)})
    server = create_server(runtime, ledger, traces, admin_token=ADMIN_TOKEN,
                           host="127.0.0.1", port=port, shard_index=shard_index,
                           manifest=manifest)
    p = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{p}", server, manifest


def test_tenant_sharding_routes_to_owning_instance() -> None:
    """S5-1: zwei Instanzen; jede bedient nur ihre Tenants, weist fremde ab (421)."""
    base0, s0, manifest = _shard_server(0, 2)
    base1, s1, _ = _shard_server(1, 2)
    try:
        # Finde je einen Tenant, der Shard 0 bzw. 1 gehört.
        t0 = next(f"t{i}" for i in range(100) if manifest.owner_index(f"t{i}") == 0)
        t1 = next(f"t{i}" for i in range(100) if manifest.owner_index(f"t{i}") == 1)

        k0 = _call(f"{base0}/api/keys", {"tenant": t0}, headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
        # Eigener Tenant auf eigenem Shard: läuft.
        assert _call(f"{base0}/api/run", {"goal": "hi"}, headers={"X-API-Key": k0})["status"]

        # Fremder Tenant (gehört Shard 1) auf Shard 0: 421 + Ziel-Shard.
        k1_on_0 = _call(f"{base0}/api/keys", {"tenant": t1},
                        headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
        status, body = _post_expect(f"{base0}/api/run", {"goal": "hi"},
                                    headers={"X-API-Key": k1_on_0})
        assert status == 421
        assert body["shard"]["index"] == 1 and "shard-1" in body["shard"]["base_url"]

        # /api/shards liefert das Manifest.
        shards = _call(f"{base1}/api/shards")
        assert shards["this_shard"] == 1 and shards["total"] == 2
    finally:
        s0.shutdown()
        s1.shutdown()


def test_drain_blocks_new_runs_but_serves_reads() -> None:
    """S5-4: Drain weist neue Runs mit 503 ab, Lese-Endpunkte bleiben offen."""
    base, server, _ = _shard_server(0, 1)
    try:
        key = _call(f"{base}/api/keys", {"tenant": "acme"},
                    headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
        # Drain aktivieren (Admin).
        assert _status_of(lambda: _call(f"{base}/api/admin/drain", {"draining": True},
                                        headers={"X-Admin-Token": "wrong"})) == 403
        drain = _call(f"{base}/api/admin/drain", {"draining": True},
                      headers={"X-Admin-Token": ADMIN_TOKEN})
        assert drain["draining"] is True

        status, body = _post_expect(f"{base}/api/run", {"goal": "hi"},
                                    headers={"X-API-Key": key})
        assert status == 503 and body["error"] == "shard draining"
        # Lesen bleibt möglich.
        assert _call(f"{base}/api/usage", headers={"X-API-Key": key})["tenant"] == "acme"

        # Drain wieder aus → Runs laufen erneut.
        _call(f"{base}/api/admin/drain", {"draining": False},
              headers={"X-Admin-Token": ADMIN_TOKEN})
        assert _call(f"{base}/api/run", {"goal": "hi"}, headers={"X-API-Key": key})["status"]
    finally:
        server.shutdown()


def test_metrics_endpoint_prometheus_format() -> None:
    """S5-5: /api/metrics liefert Prometheus-Textformat mit Run-/Kosten-Zählern."""
    base, server, _ = _shard_server(0, 1)
    try:
        key = _call(f"{base}/api/keys", {"tenant": "acme", "budget_usd": 5.0},
                    headers={"X-Admin-Token": ADMIN_TOKEN})["api_key"]
        _call(f"{base}/api/run", {"goal": "Rechne 6*7"}, headers={"X-API-Key": key})

        request = urllib.request.Request(f"{base}/api/metrics")
        with urllib.request.urlopen(request) as response:
            assert response.headers["Content-Type"].startswith("text/plain")
            text = response.read().decode()
        assert 'agent_runs_total{status="ok"} 1' in text
        assert "agent_spent_usd " in text
        assert "agent_shard_index 0" in text
        assert "# TYPE agent_inflight_runs gauge" in text
    finally:
        server.shutdown()


def test_tools_and_health_endpoints(api: str) -> None:
    tools = _call(f"{api}/api/tools")
    names = [t["name"] for t in tools["tools"]]
    assert "calc" in names and "memory_search" in names
    assert tools["sandbox_hardened"] is True

    health = _call(f"{api}/api/health")
    assert health == {"status": "ok", "service": "agent-layer"}
