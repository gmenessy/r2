"""HTTP-Server für den Coding-Agent-Guard (headless, auf brainfump.webkit).

Routen:
    POST /api/report      Entwicklungsereignis melden
    POST /api/gate        Pre-Edit Check
    GET  /api/summary     Markdown-Projektgedächtnis (?repo=)
    GET  /api/stats       Kennzahlen (?repo=)
"""

from __future__ import annotations

import os
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import BrainFumpKernel, __version__  # noqa: E402
from brainfump.webkit import Request, Response, WebApp, require, serve, text_response  # noqa: E402
from apps.coding_agent_guard.guard import CodingAgentGuard  # noqa: E402


def build_app(guard: CodingAgentGuard) -> WebApp:
    app = WebApp()
    app.health("coding-agent-guard", __version__)

    @app.get("/api/summary")
    def _summary(request: Request) -> Response:
        repo = request.query.get("repo", "default")
        max_items = int(request.query.get("max_items", 10))
        return text_response(guard.summary(repo, max_items=max_items), "text/markdown; charset=utf-8")

    @app.get("/api/stats")
    def _stats(request: Request) -> dict:
        return guard.stats(request.query.get("repo", "default"))

    @app.post("/api/report")
    def _report(request: Request) -> dict:
        body = request.json()
        require(body, "report_type")
        return guard.report(
            body.get("repo", "default"),
            body["report_type"],
            body.get("content", ""),
            **body.get("payload", {}),
        )

    @app.post("/api/gate")
    def _gate(request: Request) -> dict:
        body = request.json()
        return guard.gate(
            body.get("repo", "default"),
            action_type=body.get("action_type", "edit"),
            files=body.get("files"),
            error_signature=body.get("error_signature"),
            context=body.get("context"),
        )

    return app


def create_server(guard: CodingAgentGuard, host: str = "0.0.0.0", port: int = 8020) -> ThreadingHTTPServer:
    return serve(build_app(guard), host=host, port=port)


def main() -> None:  # pragma: no cover - manueller Einstiegspunkt
    import argparse

    parser = argparse.ArgumentParser(description="Coding-Agent-Guard")
    parser.add_argument("--data", default=os.environ.get("BRAINFUMP_DATA", "./data"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8020")))
    args = parser.parse_args()

    guard = CodingAgentGuard(BrainFumpKernel(args.data))
    server = create_server(guard, port=args.port)
    print(f"Coding-Agent-Guard auf http://0.0.0.0:{args.port} (Daten: {args.data})")
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
