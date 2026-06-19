"""HTTP-Server für den Prompt-Optimizer (auf brainfump.webkit).

Routen:
    GET  /                        Vanilla-JS-UI
    POST /api/check               Pre-Test Gate für eine Prompt-Variante
    POST /api/experiments         Experiment (Prompt + Score) aufzeichnen
    POST /api/corrections         Korrektur loggen + als Regel kompilieren
    POST /api/outputs/validate    Output gegen kompilierte Regeln prüfen
    GET  /api/memory?project=     aktive Memory Cards
    GET  /api/metrics?project=    Memory-/Optimierungs-Metriken
"""

from __future__ import annotations

import os
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import BrainFumpKernel  # noqa: E402
from brainfump.webkit import Request, WebApp, require, serve  # noqa: E402
from apps.prompt_optimizer.optimizer import PromptOptimizer  # noqa: E402

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def build_app(optimizer: PromptOptimizer) -> WebApp:
    app = WebApp(static_dir=_STATIC_DIR)
    app.static("/", "index.html").static("/index.html", "index.html").static("/app.js", "app.js")

    @app.get("/api/memory")
    def _memory(request: Request) -> dict:
        return {"cards": optimizer.memory(request.query.get("project", "default"))}

    @app.get("/api/metrics")
    def _metrics(request: Request) -> dict:
        return optimizer.metrics(request.query.get("project", "default"))

    @app.post("/api/check")
    def _check(request: Request) -> dict:
        body = request.json()
        require(body, "prompt_text")
        return optimizer.check_variant(body.get("project", "default"), body["prompt_text"])

    @app.post("/api/experiments")
    def _experiments(request: Request) -> dict:
        body = request.json()
        require(body, "prompt_text", "score")
        return optimizer.record_experiment(
            body.get("project", "default"),
            body["prompt_text"],
            float(body["score"]),
            task=body.get("task", "default"),
            notes=body.get("notes", ""),
        )

    @app.post("/api/corrections")
    def _corrections(request: Request) -> dict:
        body = request.json()
        require(body, "text")
        return optimizer.add_correction(
            body.get("project", "default"),
            body["text"],
            must_contain=body.get("must_contain"),
            must_not_contain=body.get("must_not_contain"),
            severity=body.get("severity", "warning"),
        )

    @app.post("/api/outputs/validate")
    def _validate(request: Request) -> dict:
        body = request.json()
        require(body, "output")
        return optimizer.validate_output(body.get("project", "default"), body["output"])

    return app


def create_server(optimizer: PromptOptimizer, host: str = "0.0.0.0", port: int = 8030) -> ThreadingHTTPServer:
    return serve(build_app(optimizer), host=host, port=port)


def main() -> None:  # pragma: no cover - manueller Einstiegspunkt
    import argparse

    parser = argparse.ArgumentParser(description="Prompt-Optimizer mit Langzeitgedächtnis")
    parser.add_argument("--data", default=os.environ.get("BRAINFUMP_DATA", "./data"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8030")))
    args = parser.parse_args()

    optimizer = PromptOptimizer(BrainFumpKernel(args.data))
    server = create_server(optimizer, port=args.port)
    print(f"Prompt-Optimizer auf http://0.0.0.0:{args.port} (Daten: {args.data})")
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
