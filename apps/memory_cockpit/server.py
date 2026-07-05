"""HTTP-Server für das Memory Cockpit (webkit).

Routen:
    GET  /                     Vanilla-JS-Cockpit (interaktiver Graph)
    GET  /api/cases            Akten
    GET  /api/graph?case=      Knoten + Kanten des Wissensgraphen
    GET  /api/card?id=         Karte + Beziehungen/Provenienz
    GET  /api/wiki?case=       Markdown-Projektion
    GET  /api/timeline?case=   Ereignis-Verlauf
    POST /api/simulate         Aktion live durch den Gatekeeper
"""

from __future__ import annotations

import os
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import __version__  # noqa: E402
from brainfump.webkit import Request, Response, WebApp, require, serve, text_response  # noqa: E402
from apps.memory_cockpit.cockpit import Cockpit, build_cockpit  # noqa: E402

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def build_app(cockpit: Cockpit) -> WebApp:
    app = WebApp(static_dir=_STATIC_DIR)
    app.health("memory-cockpit", __version__)
    app.static("/", "index.html").static("/index.html", "index.html").static("/app.js", "app.js")

    default_case = cockpit.demo_case

    @app.get("/api/cases")
    def _cases(request: Request) -> dict:
        return {"cases": cockpit.cases(), "default": default_case}

    @app.get("/api/graph")
    def _graph(request: Request) -> dict:
        return cockpit.graph_data(request.query.get("case", default_case))

    @app.get("/api/card")
    def _card(request: Request) -> dict:
        card_id = request.query.get("id")
        if not card_id:
            raise ValueError("missing id")
        return cockpit.card_detail(card_id)

    @app.get("/api/wiki")
    def _wiki(request: Request) -> Response:
        return text_response(cockpit.wiki(request.query.get("case", default_case)),
                             "text/markdown; charset=utf-8")

    @app.get("/api/timeline")
    def _timeline(request: Request) -> dict:
        return {"events": cockpit.timeline(request.query.get("case", default_case))}

    @app.post("/api/simulate")
    def _simulate(request: Request) -> dict:
        body = request.json()
        require(body, "action_type")
        action = {
            "action_type": body["action_type"],
            "files": body.get("files"),
            "error_signature": body.get("error_signature"),
            "context": body.get("context"),
        }
        return cockpit.simulate(body.get("case", default_case), action)

    return app


def create_server(cockpit: Cockpit, host: str = "0.0.0.0", port: int = 8050) -> ThreadingHTTPServer:
    return serve(build_app(cockpit), host=host, port=port)


def main() -> None:  # pragma: no cover - manueller Einstiegspunkt
    import argparse

    parser = argparse.ArgumentParser(description="Memory Cockpit")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8050")))
    args = parser.parse_args()

    server = create_server(build_cockpit(), port=args.port)
    print(f"Memory Cockpit auf http://0.0.0.0:{args.port}")
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
