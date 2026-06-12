"""HTTP-Server für die Agentische Akte (Stdlib, framework-frei).

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

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import BrainFumpKernel  # noqa: E402
from apps.agentic_akte.akte import AgenticAkte  # noqa: E402

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_server(akte: AgenticAkte, host: str = "0.0.0.0", port: int = 8010) -> ThreadingHTTPServer:
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
            case = params.get("case", "default")
            if url.path == "/api/cases":
                self._send(200, {"cases": akte.cases()})
            elif url.path == "/api/timeline":
                self._send(200, {"events": akte.timeline(case)})
            elif url.path == "/api/memory":
                self._send(200, {"cards": akte.memory(case)})
            elif url.path == "/api/deadlines":
                self._send(200, {"deadlines": akte.deadlines(case)})
            else:
                self._send(404, {"error": f"unknown route: {url.path}"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                case = payload.get("case", "default")
                if self.path == "/api/documents":
                    self._send(
                        200,
                        akte.add_document(
                            case,
                            payload["name"],
                            payload.get("summary", ""),
                            fragile=bool(payload.get("fragile")),
                            reason=payload.get("reason", ""),
                        ),
                    )
                elif self.path == "/api/decisions":
                    self._send(200, akte.record_decision(case, payload["text"], payload.get("scope")))
                elif self.path == "/api/deadlines":
                    self._send(200, akte.add_deadline(case, payload["description"], payload["due_date"]))
                elif self.path == "/api/open-points":
                    self._send(200, akte.add_open_point(case, payload["text"]))
                elif self.path == "/api/failed-analyses":
                    self._send(
                        200,
                        akte.record_failed_analysis(
                            case,
                            payload["text"],
                            payload["signature"],
                            alternative=payload.get("alternative", ""),
                        ),
                    )
                elif self.path == "/api/check":
                    self._send(
                        200,
                        akte.check_action(
                            case,
                            payload.get("action_type", "modify_document"),
                            documents=payload.get("documents"),
                            error_signature=payload.get("error_signature"),
                        ),
                    )
                elif self.path == "/api/consolidate":
                    self._send(200, akte.consolidate(case))
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
            with open(os.path.join(_STATIC_DIR, name), "rb") as fh:
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
