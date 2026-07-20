"""HTTP-Server der Agent Ops Console (auf brainfump.webkit).

Routen:
    GET  /                       Ops-Console-UI (Vanilla JS)
    GET  /api/state              LLM-Modus, Rate-Limit-Konfig, Tenant, letzte Runs
    GET  /api/scenarios          die drei Demo-Szenarien
    POST /api/scenarios/run      {"name": "live_triage"} → Run/Queued/Rate-Limited
    GET  /api/runs?run_id=       Zustand/Ergebnis eines async Runs
    GET  /api/runs/stream?run_id=  Live-Trace als Server-Sent Events
    GET  /api/trace?run_id=      vollständiger xAI-Trace
    GET  /api/explain?run_id=    Begründung + Kosten-Breakdown
"""

from __future__ import annotations

import os
import sys
from http.server import ThreadingHTTPServer

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
from apps.agent_layer.streaming import trace_sse_events  # noqa: E402
from apps.agent_layer.xai import TraceStore  # noqa: E402
from apps.agent_ops_console.console import OpsConsole  # noqa: E402

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def build_app(console: OpsConsole) -> WebApp:
    app = WebApp(static_dir=_STATIC_DIR)
    app.health("agent-ops-console", __version__)
    app.static("/", "index.html").static("/index.html", "index.html").static("/app.js", "app.js")

    @app.get("/api/state")
    def _state(_: Request) -> dict:
        return console.state()

    @app.get("/api/scenarios")
    def _scenarios(_: Request) -> dict:
        return {"scenarios": console.scenarios()}

    @app.post("/api/scenarios/run")
    def _run_scenario(request: Request):
        body = request.json()
        require(body, "name")
        try:
            result = console.run_scenario(body["name"])
        except ValueError as exc:
            raise HttpError(400, str(exc))
        except LLMError as exc:
            raise HttpError(502, str(exc))
        if result["status"] == "rate_limited":
            retry_after = result["retry_after_s"]
            return json_response(result, 429,
                                 headers={"Retry-After": str(max(1, int(retry_after + 0.999)))})
        return json_response(result, 202 if result.get("async") else 200)

    @app.get("/api/runs")
    def _run_status(request: Request) -> dict:
        run_id = request.query.get("run_id")
        if not run_id:
            raise HttpError(400, "missing query parameter: run_id")
        state = console.run_status(run_id)
        if state is None:
            raise HttpError(404, f"unknown async run: {run_id}")
        return {"run_id": run_id, "status": state["status"], "result": state["result"]}

    @app.get("/api/runs/stream")
    def _stream(request: Request):
        run_id = request.query.get("run_id")
        if not run_id:
            raise HttpError(400, "missing query parameter: run_id")
        if console.traces.trace(run_id) is None:
            # Async eingereiht, aber noch nicht gestartet? Auf begin() warten
            # statt sofort mit "unknown run" abzubrechen (Single-Tenant-Demo,
            # daher keine Auth nötig — anders als in der Plattform selbst).
            if console.run_status(run_id) is None:
                raise HttpError(404, f"unknown run: {run_id}")
            return StreamingResponse(trace_sse_events(console.traces, run_id, wait_for_begin=True))
        return StreamingResponse(trace_sse_events(console.traces, run_id))

    @app.get("/api/trace")
    def _trace(request: Request) -> dict:
        run_id = request.query.get("run_id")
        if not run_id:
            raise HttpError(400, "missing query parameter: run_id")
        trace = console.traces.trace(run_id)
        if trace is None:
            raise HttpError(404, f"unknown run: {run_id}")
        return trace

    @app.get("/api/explain")
    def _explain(request: Request) -> dict:
        run_id = request.query.get("run_id")
        if not run_id:
            raise HttpError(400, "missing query parameter: run_id")
        explanation = console.traces.explain(run_id)
        if explanation is None:
            raise HttpError(404, f"unknown run: {run_id}")
        explanation["cost"] = console.ledger.run_cost(run_id)
        return explanation

    return app


def create_server(console: OpsConsole, host: str = "0.0.0.0",
                  port: int = 8080) -> ThreadingHTTPServer:
    return serve(build_app(console), host=host, port=port)


def main() -> None:  # pragma: no cover - manueller Einstiegspunkt
    import argparse

    parser = argparse.ArgumentParser(description="Agent Ops Console — Async/Streaming-Demo")
    parser.add_argument("--data", default=os.environ.get("AGENT_DATA", "./data"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    args = parser.parse_args()

    os.makedirs(args.data, exist_ok=True)
    kernel = BrainFumpKernel(os.path.join(args.data, "memory"))
    ledger = BillingLedger(os.path.join(args.data, "billing.db"))
    traces = TraceStore(os.path.join(args.data, "traces.db"))

    # Default: SimulatedLLM (offline erlebbar). AGENT_SIM=0 → echtes Modell via vLLM.
    llm = VLLMClient() if os.environ.get("AGENT_SIM", "1").lower() in ("0", "false") else None
    console = OpsConsole(kernel, ledger, traces, llm=llm)

    server = create_server(console, port=args.port)
    mode = "SimulatedLLM (offline)" if llm is None else f"{llm.model} @ {llm.base_url}"
    print(f"Agent Ops Console auf http://0.0.0.0:{args.port} (LLM: {mode}, Daten: {args.data})")
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
