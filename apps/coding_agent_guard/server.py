"""HTTP-Server für den Coding-Agent-Guard (headless, Stdlib).

Routen:
    POST /api/report      Entwicklungsereignis melden
    POST /api/gate        Pre-Edit Check
    GET  /api/summary     Markdown-Projektgedächtnis (?repo=)
    GET  /api/stats       Kennzahlen (?repo=)
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
from apps.coding_agent_guard.guard import CodingAgentGuard  # noqa: E402


def create_server(guard: CodingAgentGuard, host: str = "0.0.0.0", port: int = 8020) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            repo = params.get("repo", "default")
            if url.path == "/api/summary":
                data = guard.summary(repo, max_items=int(params.get("max_items", 10))).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif url.path == "/api/stats":
                self._send(200, guard.stats(repo))
            else:
                self._send(404, {"error": f"unknown route: {url.path}"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                repo = payload.get("repo", "default")
                if self.path == "/api/report":
                    self._send(
                        200,
                        guard.report(
                            repo,
                            payload["report_type"],
                            payload.get("content", ""),
                            **payload.get("payload", {}),
                        ),
                    )
                elif self.path == "/api/gate":
                    self._send(
                        200,
                        guard.gate(
                            repo,
                            action_type=payload.get("action_type", "edit"),
                            files=payload.get("files"),
                            error_signature=payload.get("error_signature"),
                            context=payload.get("context"),
                        ),
                    )
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

        def log_message(self, *args: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


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
