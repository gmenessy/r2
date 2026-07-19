"""HTTP-API des Agent Execution Layer (auf brainfump.webkit, API-first).

Routen:
    POST /api/keys                API-Key ausstellen (X-Admin-Token)
    POST /api/run                 Agent-Run starten (X-API-Key)
    GET  /api/trace?run_id=       xAI-Trace — nur eigener Tenant oder Admin
    GET  /api/explain?run_id=     Begründung + Kosten — nur eigener Tenant oder Admin
    GET  /api/usage               Verbrauch + Budget des eigenen Tenants (X-API-Key)
    GET  /api/tools               registrierte Tools inkl. Sandbox-Limits
    GET  /api/health, /api/version

Konfiguration (Umgebung):
    VLLM_BASE_URL   OpenAI-kompatibler vLLM-Endpunkt (Default http://vllm:8000/v1)
    AGENT_MODEL     Modellname (Default gemini4-31b)
    ADMIN_TOKEN     Pflicht für /api/keys
    AGENT_DATA      Datenverzeichnis (SQLite: Memory, Billing, Traces)
"""

from __future__ import annotations

import hmac
import json
import os
import sys
import time
import uuid
from http.server import ThreadingHTTPServer
from typing import Iterator

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import BrainFumpKernel, __version__  # noqa: E402
from brainfump.webkit import (  # noqa: E402
    HttpError,
    Request,
    StreamingResponse,
    WebApp,
    json_response,
    require,
    serve,
)
from apps.agent_layer.billing import BillingLedger  # noqa: E402
from apps.agent_layer.llm import LLMError, VLLMClient  # noqa: E402
from apps.agent_layer.ratelimit import RateLimiter  # noqa: E402
from apps.agent_layer.runner import (  # noqa: E402
    AsyncRunner,
    QueueFullError,
    TenantQueueFullError,
)
from apps.agent_layer.runtime import AgentRuntime  # noqa: E402
from apps.agent_layer.tools import builtin_registry  # noqa: E402
from apps.agent_layer.xai import TraceStore  # noqa: E402


def build_app(runtime: AgentRuntime, ledger: BillingLedger, traces: TraceStore,
              admin_token: str | None = None,
              rate_limiter: RateLimiter | None = None,
              async_runner: "AsyncRunner | None" = None) -> WebApp:
    app = WebApp()
    app.health("agent-layer", __version__)

    def _tenant(request: Request) -> str:
        api_key = request.header("x-api-key") or request.query.get("api_key")
        tenant = ledger.resolve(api_key) if api_key else None
        if tenant is None:
            raise HttpError(401, "missing or unknown API key")
        return tenant

    def _is_admin(request: Request) -> bool:
        supplied = request.header("x-admin-token")
        # hmac.compare_digest: konstante Laufzeit gegen Timing-Angriffe (F5).
        return (admin_token is not None and supplied is not None
                and hmac.compare_digest(supplied, admin_token))

    def _authorized_trace(request: Request) -> dict:
        """Trace laden und Zugriff prüfen: eigener Tenant oder Admin (F1) —
        Traces enthalten Ziele, Tool-Argumente und Memory-Inhalte."""
        run_id = request.query.get("run_id")
        if not run_id:
            raise HttpError(400, "missing query parameter: run_id")
        trace = traces.trace(run_id)
        if not _is_admin(request):
            tenant = _tenant(request)  # 401 ohne gültigen Key — auch für unbekannte Runs
            if trace is not None and trace["tenant"] != tenant:
                raise HttpError(403, "run belongs to another tenant")
        if trace is None:
            raise HttpError(404, f"unknown run: {run_id}")
        return trace

    @app.post("/api/keys")
    def _keys(request: Request) -> dict:
        body = request.json()
        require(body, "tenant")
        if admin_token is None:
            raise HttpError(503, "ADMIN_TOKEN not configured")
        if not _is_admin(request):
            raise HttpError(403, "invalid admin token")
        ttl = body.get("ttl_seconds")
        api_key = ledger.create_key(body["tenant"], float(body.get("budget_usd", 10.0)),
                                    ttl_seconds=float(ttl) if ttl is not None else None)
        return {"tenant": body["tenant"], "api_key": api_key,
                "budget_usd": float(body.get("budget_usd", 10.0))}

    @app.post("/api/keys/rotate")
    def _rotate(request: Request) -> dict:
        """Eigenen Key rotieren: neuer Key, alter läuft im Kulanzfenster aus (S3-4)."""
        api_key = request.header("x-api-key") or request.query.get("api_key")
        if not api_key or ledger.resolve(api_key) is None:
            raise HttpError(401, "missing or unknown API key")
        grace = float(request.json().get("grace_seconds", 300.0))
        new_key = ledger.rotate_key(api_key, grace_seconds=grace)
        if new_key is None:
            raise HttpError(401, "key no longer valid")
        return {"api_key": new_key, "old_key_grace_seconds": grace}

    def _retry(status: int, error: str, retry_after: float):
        return json_response(
            {"error": error, "retry_after_s": round(retry_after, 2)},
            status, headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
        )

    @app.post("/api/run")
    def _run(request: Request):
        body = request.json()
        require(body, "goal")
        tenant = _tenant(request)
        if rate_limiter is not None:
            allowed, retry_after = rate_limiter.acquire(tenant)
            if not allowed:
                # 429 mit Retry-After — der Client weiß, wann er es erneut darf.
                return _retry(429, "rate limit exceeded", retry_after)

        # Async-Pfad (S4-2): sofort run_id zurück, Ausführung im Hintergrund.
        if body.get("async") and async_runner is not None:
            try:
                run_id = async_runner.submit(body["goal"], tenant, case_id=body.get("case_id"))
            except TenantQueueFullError as exc:
                return _retry(429, "too many concurrent runs", exc.retry_after_s)
            except QueueFullError as exc:  # global überlastet → 503 (S4-5)
                return _retry(503, "run queue full", exc.retry_after_s)
            return json_response({"run_id": run_id, "status": "queued"}, 202)

        try:
            result = runtime.run(body["goal"], tenant=tenant, case_id=body.get("case_id"))
        except LLMError as exc:
            raise HttpError(502, str(exc))
        return result.to_dict()

    @app.get("/api/runs")
    def _run_status(request: Request) -> dict:
        """Zustand/Ergebnis eines async gestarteten Runs (S4-2). Nur eigener Tenant."""
        run_id = request.query.get("run_id")
        if not run_id:
            raise HttpError(400, "missing query parameter: run_id")
        tenant = _tenant(request)
        state = async_runner.status(run_id) if async_runner is not None else None
        if state is None:
            raise HttpError(404, f"unknown async run: {run_id}")
        if state["tenant"] != tenant and not _is_admin(request):
            raise HttpError(403, "run belongs to another tenant")
        return {"run_id": run_id, "status": state["status"], "result": state["result"]}

    _TERMINAL = {"ok", "error", "budget_exceeded", "max_steps", "llm_error"}

    def _sse_events(run_id: str, poll_s: float = 0.05, timeout_s: float = 120.0) -> Iterator[bytes]:
        """Trace-Schritte live als Server-Sent Events, bis der Run terminal ist."""
        sent = 0
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            trace = traces.trace(run_id)
            if trace is None:
                yield b"event: error\ndata: {\"error\": \"unknown run\"}\n\n"
                return
            for step in trace["steps"][sent:]:
                yield f"event: step\ndata: {json.dumps(step, ensure_ascii=False)}\n\n".encode()
            sent = len(trace["steps"])
            if trace["status"] in _TERMINAL:
                done = {"status": trace["status"], "answer": trace["answer"]}
                yield f"event: done\ndata: {json.dumps(done, ensure_ascii=False)}\n\n".encode()
                return
            time.sleep(poll_s)
        yield b"event: timeout\ndata: {}\n\n"

    @app.get("/api/runs/stream")
    def _stream(request: Request):
        """SSE-Stream der Trace-Schritte eines Runs (S4-3). Nur eigener Tenant/Admin."""
        trace = _authorized_trace(request)  # 400/401/403/404 + Zugriffsprüfung
        return StreamingResponse(_sse_events(trace["run_id"]))

    @app.get("/api/trace")
    def _trace(request: Request) -> dict:
        return _authorized_trace(request)

    @app.get("/api/explain")
    def _explain(request: Request) -> dict:
        trace = _authorized_trace(request)
        explanation = traces.explain(trace["run_id"])
        explanation["cost"] = ledger.run_cost(trace["run_id"])
        return explanation

    @app.get("/api/usage")
    def _usage(request: Request) -> dict:
        return ledger.usage(_tenant(request))

    @app.get("/api/tools")
    def _tools(_: Request) -> dict:
        return {"tools": runtime.registry.describe(),
                "sandbox_hardened": runtime.sandbox.hardened}

    return app


def create_server(runtime: AgentRuntime, ledger: BillingLedger, traces: TraceStore,
                  admin_token: str | None = None, host: str = "0.0.0.0",
                  port: int = 8060, rate_limiter: RateLimiter | None = None,
                  async_runner: AsyncRunner | None = None) -> ThreadingHTTPServer:
    return serve(build_app(runtime, ledger, traces, admin_token=admin_token,
                           rate_limiter=rate_limiter, async_runner=async_runner),
                 host=host, port=port)


def main() -> None:  # pragma: no cover - manueller Einstiegspunkt
    import argparse

    parser = argparse.ArgumentParser(description="Agent Execution Layer")
    parser.add_argument("--data", default=os.environ.get("AGENT_DATA", "./data"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8060")))
    parser.add_argument("--retention-days", type=float,
                        default=float(os.environ.get("AGENT_RETENTION_DAYS", "0")) or None,
                        help="Traces älter als N Tage beim Start löschen (0/leer = aus)")
    parser.add_argument("--rate-per-minute", type=int,
                        default=int(os.environ.get("AGENT_RATE_PER_MINUTE", "0")),
                        help="Runs pro Minute je Tenant (0 = kein Limit)")
    parser.add_argument("--async-workers", type=int,
                        default=int(os.environ.get("AGENT_ASYNC_WORKERS", "4")),
                        help="Worker-Threads für async Runs (0 = async deaktiviert)")
    args = parser.parse_args()

    os.makedirs(args.data, exist_ok=True)
    kernel = BrainFumpKernel(os.path.join(args.data, "memory"))
    ledger = BillingLedger(os.path.join(args.data, "billing.db"))
    traces = TraceStore(os.path.join(args.data, "traces.db"))
    if args.retention_days:
        removed = traces.prune(older_than_days=args.retention_days)
        print(f"Retention: {removed} Runs älter als {args.retention_days} Tage entfernt")
    # AGENT_SIM=1 → deterministischer LLM-Ersatz: die komplette Plattform
    # läuft offline (Demo, Integrationstests, CI) — kein vLLM nötig.
    if os.environ.get("AGENT_SIM", "").lower() in ("1", "true", "yes"):
        from apps.agent_layer.simllm import SimulatedLLM

        llm: VLLMClient | SimulatedLLM = SimulatedLLM()
    else:
        llm = VLLMClient()
    runtime = AgentRuntime(
        llm=llm,
        registry=builtin_registry(kernel),
        traces=traces,
        kernel=kernel,
        ledger=ledger,
    )
    admin_token = os.environ.get("ADMIN_TOKEN") or f"admin_{uuid.uuid4().hex}"
    if not os.environ.get("ADMIN_TOKEN"):
        print(f"ADMIN_TOKEN nicht gesetzt — generiert für diese Instanz: {admin_token}")

    rate_limiter = RateLimiter(args.rate_per_minute) if args.rate_per_minute > 0 else None
    async_runner = AsyncRunner(runtime, max_workers=args.async_workers) \
        if args.async_workers > 0 else None
    server = create_server(runtime, ledger, traces, admin_token=admin_token, port=args.port,
                           rate_limiter=rate_limiter, async_runner=async_runner)
    print(f"Agent Execution Layer auf http://0.0.0.0:{args.port} "
          f"(LLM: {runtime.llm.model} @ {runtime.llm.base_url}, Daten: {args.data})")
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
