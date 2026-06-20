"""HTTP-API für den Memory Gatekeeper (Sprint 2: /api/gatekeeper/check).

Auf dem framework-freien :mod:`brainfump.webkit` aufgesetzt, damit der
Kernel ohne externe Pakete lauffähig bleibt. Die Handler-Funktionen sind
von Routing/Transport getrennt und so einzeln testbar.
"""

from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import Any

from brainfump import __version__
from brainfump.kernel import BrainFumpKernel
from brainfump.webkit import Request, WebApp, require, serve


def handle_gatekeeper_check(kernel: BrainFumpKernel, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or "action" not in payload:
        raise ValueError("payload must contain an 'action' object")
    decision = kernel.check_action(payload["action"])
    return decision.to_dict()


def handle_search(kernel: BrainFumpKernel, payload: dict[str, Any]) -> dict[str, Any]:
    require(payload, "query")
    results = kernel.search(payload["query"], case_id=payload.get("case_id"), k=payload.get("k", 5))
    return {
        "results": [
            {"score": round(r.score, 4), "card": r.card.to_dict()} for r in results
        ]
    }


def build_app(kernel: BrainFumpKernel) -> WebApp:
    app = WebApp()
    app.health("gatekeeper-api", __version__)

    @app.post("/api/gatekeeper/check")
    def _check(request: Request) -> dict[str, Any]:
        return handle_gatekeeper_check(kernel, request.json())

    @app.post("/api/memory/search")
    def _search(request: Request) -> dict[str, Any]:
        return handle_search(kernel, request.json())

    return app


def create_server(kernel: BrainFumpKernel, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    return serve(build_app(kernel), host=host, port=port)


def main() -> None:  # pragma: no cover - manueller Einstiegspunkt
    import argparse

    parser = argparse.ArgumentParser(description="BrainFump Gatekeeper API")
    parser.add_argument("--data", default="./brainfump_data", help="Speicherpfad")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    kernel = BrainFumpKernel(args.data)
    server = create_server(kernel, port=args.port)
    print(f"BrainFump Gatekeeper API auf http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
