"""HTTP-API des Agent Execution Layer (auf brainfump.webkit, API-first).

Routen:
    POST /api/keys                API-Key ausstellen (X-Admin-Token)
    POST /api/run                 Agent-Run starten (X-API-Key)
    GET  /api/trace?run_id=       vollständiger xAI-Trace eines Runs
    GET  /api/explain?run_id=     verdichtete Begründung (Memory/Tools/Kosten)
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

import os
import sys
import uuid
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import BrainFumpKernel, __version__  # noqa: E402
from brainfump.webkit import HttpError, Request, WebApp, require, serve  # noqa: E402
from apps.agent_layer.billing import BillingLedger  # noqa: E402
from apps.agent_layer.llm import LLMError, VLLMClient  # noqa: E402
from apps.agent_layer.runtime import AgentRuntime  # noqa: E402
from apps.agent_layer.tools import builtin_registry  # noqa: E402
from apps.agent_layer.xai import TraceStore  # noqa: E402


def build_app(runtime: AgentRuntime, ledger: BillingLedger, traces: TraceStore,
              admin_token: str | None = None) -> WebApp:
    app = WebApp()
    app.health("agent-layer", __version__)

    def _tenant(request: Request) -> str:
        api_key = request.header("x-api-key") or request.query.get("api_key")
        tenant = ledger.resolve(api_key) if api_key else None
        if tenant is None:
            raise HttpError(401, "missing or unknown API key")
        return tenant

    @app.post("/api/keys")
    def _keys(request: Request) -> dict:
        body = request.json()
        require(body, "tenant")
        if admin_token is None:
            raise HttpError(503, "ADMIN_TOKEN not configured")
        if request.header("x-admin-token") != admin_token:
            raise HttpError(403, "invalid admin token")
        api_key = ledger.create_key(body["tenant"], float(body.get("budget_usd", 10.0)))
        return {"tenant": body["tenant"], "api_key": api_key,
                "budget_usd": float(body.get("budget_usd", 10.0))}

    @app.post("/api/run")
    def _run(request: Request) -> dict:
        body = request.json()
        require(body, "goal")
        tenant = _tenant(request)
        try:
            result = runtime.run(body["goal"], tenant=tenant, case_id=body.get("case_id"))
        except LLMError as exc:
            raise HttpError(502, str(exc))
        return result.to_dict()

    @app.get("/api/trace")
    def _trace(request: Request) -> dict:
        run_id = request.query.get("run_id")
        if not run_id:
            raise HttpError(400, "missing query parameter: run_id")
        trace = traces.trace(run_id)
        if trace is None:
            raise HttpError(404, f"unknown run: {run_id}")
        return trace

    @app.get("/api/explain")
    def _explain(request: Request) -> dict:
        run_id = request.query.get("run_id")
        if not run_id:
            raise HttpError(400, "missing query parameter: run_id")
        explanation = traces.explain(run_id)
        if explanation is None:
            raise HttpError(404, f"unknown run: {run_id}")
        explanation["cost"] = ledger.run_cost(run_id)
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
                  port: int = 8060) -> ThreadingHTTPServer:
    return serve(build_app(runtime, ledger, traces, admin_token=admin_token),
                 host=host, port=port)


def main() -> None:  # pragma: no cover - manueller Einstiegspunkt
    import argparse

    parser = argparse.ArgumentParser(description="Agent Execution Layer")
    parser.add_argument("--data", default=os.environ.get("AGENT_DATA", "./data"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8060")))
    args = parser.parse_args()

    os.makedirs(args.data, exist_ok=True)
    kernel = BrainFumpKernel(os.path.join(args.data, "memory"))
    ledger = BillingLedger(os.path.join(args.data, "billing.db"))
    traces = TraceStore(os.path.join(args.data, "traces.db"))
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

    server = create_server(runtime, ledger, traces, admin_token=admin_token, port=args.port)
    print(f"Agent Execution Layer auf http://0.0.0.0:{args.port} "
          f"(LLM: {runtime.llm.model} @ {runtime.llm.base_url}, Daten: {args.data})")
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
