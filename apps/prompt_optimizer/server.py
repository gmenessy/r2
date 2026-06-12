"""HTTP-Server für den Prompt-Optimizer (Stdlib, framework-frei).

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

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import BrainFumpKernel  # noqa: E402
from apps.prompt_optimizer.optimizer import PromptOptimizer  # noqa: E402

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _require(payload: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if not payload.get(k)]
    if missing:
        raise ValueError(f"missing fields: {missing}")


def create_server(optimizer: PromptOptimizer, host: str = "0.0.0.0", port: int = 8030) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                self._send_file("index.html", "text/html; charset=utf-8")
                return
            if url.path == "/app.js":
                self._send_file("app.js", "application/javascript; charset=utf-8")
                return
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            project = params.get("project", "default")
            if url.path == "/api/memory":
                self._send(200, {"cards": optimizer.memory(project)})
            elif url.path == "/api/metrics":
                self._send(200, optimizer.metrics(project))
            else:
                self._send(404, {"error": f"unknown route: {url.path}"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                project = payload.get("project", "default")
                if self.path == "/api/check":
                    _require(payload, "prompt_text")
                    self._send(200, optimizer.check_variant(project, payload["prompt_text"]))
                elif self.path == "/api/experiments":
                    _require(payload, "prompt_text")
                    if "score" not in payload:
                        raise ValueError("missing fields: ['score']")
                    self._send(
                        200,
                        optimizer.record_experiment(
                            project,
                            payload["prompt_text"],
                            float(payload["score"]),
                            task=payload.get("task", "default"),
                            notes=payload.get("notes", ""),
                        ),
                    )
                elif self.path == "/api/corrections":
                    _require(payload, "text")
                    self._send(
                        200,
                        optimizer.add_correction(
                            project,
                            payload["text"],
                            must_contain=payload.get("must_contain"),
                            must_not_contain=payload.get("must_not_contain"),
                            severity=payload.get("severity", "warning"),
                        ),
                    )
                elif self.path == "/api/outputs/validate":
                    _require(payload, "output")
                    self._send(200, optimizer.validate_output(project, payload["output"]))
                else:
                    self._send(404, {"error": f"unknown route: {self.path}"})
            except (ValueError, KeyError) as exc:
                self._send(400, {"error": str(exc)})
            except Exception as exc:  # pragma: no cover - defensive
                self._send(500, {"error": str(exc)})

        def _send(self, status: int, body: dict[str, Any]) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, name: str, content_type: str) -> None:
            path = os.path.join(_STATIC_DIR, name)
            with open(path, "rb") as fh:
                data = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


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
