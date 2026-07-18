"""HTTP-Server des Agent Flightdeck (auf brainfump.webkit).

Routen:
    GET  /                       Flightdeck-UI (Vanilla JS)
    GET  /api/state              LLM-Modus, Sandbox-Status, Tenants, letzte Runs
    GET  /api/scenarios          die vier Demo-Szenarien
    POST /api/scenarios/run      {"name": "happy_path"} → Run + run_id
    POST /api/run                {"goal": "…"} freier Run als Tenant 'demo'
    GET  /api/trace?run_id=      vollständiger xAI-Trace
    GET  /api/explain?run_id=    Begründung + Kosten-Breakdown
    GET  /api/tools              registrierte Tools inkl. Sandbox-Limits
"""

from __future__ import annotations

import os
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import BrainFumpKernel, __version__  # noqa: E402
from brainfump.webkit import HttpError, Request, WebApp, require, serve  # noqa: E402
from apps.agent_flightdeck.flightdeck import Flightdeck  # noqa: E402
from apps.agent_layer.billing import BillingLedger  # noqa: E402
from apps.agent_layer.llm import LLMError, VLLMClient  # noqa: E402
from apps.agent_layer.xai import TraceStore  # noqa: E402

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def build_app(deck: Flightdeck) -> WebApp:
    app = WebApp(static_dir=_STATIC_DIR)
    app.health("agent-flightdeck", __version__)
    app.static("/", "index.html").static("/index.html", "index.html").static("/app.js", "app.js")

    @app.get("/api/state")
    def _state(_: Request) -> dict:
        return deck.state()

    @app.get("/api/scenarios")
    def _scenarios(_: Request) -> dict:
        return {"scenarios": deck.scenarios()}

    @app.post("/api/scenarios/run")
    def _run_scenario(request: Request) -> dict:
        body = request.json()
        require(body, "name")
        try:
            return deck.run_scenario(body["name"])
        except LLMError as exc:
            raise HttpError(502, str(exc))

    @app.post("/api/run")
    def _run(request: Request) -> dict:
        body = request.json()
        require(body, "goal")
        try:
            return deck.run_goal(body["goal"], case_id=body.get("case_id"))
        except LLMError as exc:
            raise HttpError(502, str(exc))

    @app.get("/api/trace")
    def _trace(request: Request) -> dict:
        run_id = request.query.get("run_id")
        if not run_id:
            raise HttpError(400, "missing query parameter: run_id")
        trace = deck.traces.trace(run_id)
        if trace is None:
            raise HttpError(404, f"unknown run: {run_id}")
        return trace

    @app.get("/api/explain")
    def _explain(request: Request) -> dict:
        run_id = request.query.get("run_id")
        if not run_id:
            raise HttpError(400, "missing query parameter: run_id")
        explanation = deck.traces.explain(run_id)
        if explanation is None:
            raise HttpError(404, f"unknown run: {run_id}")
        explanation["cost"] = deck.ledger.run_cost(run_id)
        return explanation

    @app.get("/api/tools")
    def _tools(_: Request) -> dict:
        return {"tools": deck.runtime.registry.describe(),
                "sandbox_hardened": deck.runtime.sandbox.hardened}

    return app


def create_server(deck: Flightdeck, host: str = "0.0.0.0",
                  port: int = 8070) -> ThreadingHTTPServer:
    return serve(build_app(deck), host=host, port=port)


def main() -> None:  # pragma: no cover - manueller Einstiegspunkt
    import argparse

    parser = argparse.ArgumentParser(description="Agent Flightdeck — Demo des Agent Execution Layer")
    parser.add_argument("--data", default=os.environ.get("AGENT_DATA", "./data"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8070")))
    args = parser.parse_args()

    os.makedirs(args.data, exist_ok=True)
    kernel = BrainFumpKernel(os.path.join(args.data, "memory"))
    ledger = BillingLedger(os.path.join(args.data, "billing.db"))
    traces = TraceStore(os.path.join(args.data, "traces.db"))

    # Default: SimulatedLLM (offline erlebbar). AGENT_SIM=0 → echtes Modell via vLLM.
    llm = VLLMClient() if os.environ.get("AGENT_SIM", "1").lower() in ("0", "false") else None
    deck = Flightdeck(kernel, ledger, traces, llm=llm)

    server = create_server(deck, port=args.port)
    mode = "SimulatedLLM (offline)" if llm is None else f"{llm.model} @ {llm.base_url}"
    print(f"Agent Flightdeck auf http://0.0.0.0:{args.port} (LLM: {mode}, Daten: {args.data})")
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
