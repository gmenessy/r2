"""HTTP-Server für die Agentische Akte (auf brainfump.webkit).

Routen:
    GET  /                       Vanilla-JS-UI mit Gatekeeper-Warnkarte
    GET  /api/cases              alle Akten
    GET  /api/timeline?case=     revisionssichere Aktenhistorie
    GET  /api/memory?case=       aktive Memory Cards
    GET  /api/deadlines?case=    Fristen mit Status
    POST /api/documents          Dokument erfassen (optional fragil)
    POST /api/decisions          Entscheidung festhalten
    POST /api/deadlines          Frist anlegen
    POST /api/open-points        offenen Punkt notieren
    POST /api/failed-analyses    verworfene Analyse festhalten
    POST /api/check              Aktion gegen den Gatekeeper prüfen
    POST /api/consolidate        Dedupe/Widersprüche (offline-Lauf)
"""

from __future__ import annotations

import os
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import BrainFumpKernel  # noqa: E402
from brainfump.webkit import Request, WebApp, require, serve  # noqa: E402
from apps.agentic_akte.akte import AgenticAkte  # noqa: E402

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def build_app(akte: AgenticAkte) -> WebApp:
    app = WebApp(static_dir=_STATIC_DIR)
    app.static("/", "index.html").static("/index.html", "index.html").static("/app.js", "app.js")

    @app.get("/api/cases")
    def _cases(request: Request) -> dict:
        return {"cases": akte.cases()}

    @app.get("/api/timeline")
    def _timeline(request: Request) -> dict:
        return {"events": akte.timeline(request.query.get("case", "default"))}

    @app.get("/api/memory")
    def _memory(request: Request) -> dict:
        return {"cards": akte.memory(request.query.get("case", "default"))}

    @app.get("/api/deadlines")
    def _deadlines(request: Request) -> dict:
        return {"deadlines": akte.deadlines(request.query.get("case", "default"))}

    @app.post("/api/documents")
    def _documents(request: Request) -> dict:
        body = request.json()
        require(body, "name")
        return akte.add_document(
            body.get("case", "default"),
            body["name"],
            body.get("summary", ""),
            fragile=bool(body.get("fragile")),
            reason=body.get("reason", ""),
        )

    @app.post("/api/decisions")
    def _decisions(request: Request) -> dict:
        body = request.json()
        require(body, "text")
        return akte.record_decision(body.get("case", "default"), body["text"], body.get("scope"))

    @app.post("/api/deadlines")
    def _add_deadline(request: Request) -> dict:
        body = request.json()
        require(body, "description", "due_date")
        return akte.add_deadline(body.get("case", "default"), body["description"], body["due_date"])

    @app.post("/api/open-points")
    def _open_points(request: Request) -> dict:
        body = request.json()
        require(body, "text")
        return akte.add_open_point(body.get("case", "default"), body["text"])

    @app.post("/api/failed-analyses")
    def _failed_analyses(request: Request) -> dict:
        body = request.json()
        require(body, "text", "signature")
        return akte.record_failed_analysis(
            body.get("case", "default"),
            body["text"],
            body["signature"],
            alternative=body.get("alternative", ""),
        )

    @app.post("/api/check")
    def _check(request: Request) -> dict:
        body = request.json()
        return akte.check_action(
            body.get("case", "default"),
            body.get("action_type", "modify_document"),
            documents=body.get("documents"),
            error_signature=body.get("error_signature"),
        )

    @app.post("/api/consolidate")
    def _consolidate(request: Request) -> dict:
        return akte.consolidate(request.json().get("case", "default"))

    return app


def create_server(akte: AgenticAkte, host: str = "0.0.0.0", port: int = 8010) -> ThreadingHTTPServer:
    return serve(build_app(akte), host=host, port=port)


def main() -> None:  # pragma: no cover - manueller Einstiegspunkt
    import argparse

    parser = argparse.ArgumentParser(description="Agentische Akte")
    parser.add_argument("--data", default=os.environ.get("BRAINFUMP_DATA", "./data"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8010")))
    args = parser.parse_args()

    akte = AgenticAkte(BrainFumpKernel(args.data))
    server = create_server(akte, port=args.port)
    print(f"Agentische Akte auf http://0.0.0.0:{args.port} (Daten: {args.data})")
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
