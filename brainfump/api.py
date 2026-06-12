"""HTTP-API für den Memory Gatekeeper (Sprint 2: /api/gatekeeper/check).

Bewusst ohne Framework-Abhängigkeit (Stdlib http.server), damit der Kernel
ohne externe Pakete lauffähig bleibt. handle_request() ist davon getrennt
testbar und kann ebenso hinter FastAPI/Starlette gehängt werden.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from brainfump.kernel import BrainFumpKernel


def handle_gatekeeper_check(kernel: BrainFumpKernel, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict) or "action" not in payload:
        raise ValueError("payload must contain an 'action' object")
    decision = kernel.check_action(payload["action"])
    return decision.to_dict()


def handle_search(kernel: BrainFumpKernel, payload: dict[str, Any]) -> dict[str, Any]:
    query = payload.get("query")
    if not query:
        raise ValueError("payload must contain 'query'")
    results = kernel.search(query, case_id=payload.get("case_id"), k=payload.get("k", 5))
    return {
        "results": [
            {"score": round(r.score, 4), "card": r.card.to_dict()} for r in results
        ]
    }


_ROUTES = {
    "/api/gatekeeper/check": handle_gatekeeper_check,
    "/api/memory/search": handle_search,
}


def create_server(kernel: BrainFumpKernel, host: str = "127.0.0.1", port: int = 8080) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server API)
            handler = _ROUTES.get(self.path)
            if handler is None:
                self._send(404, {"error": f"unknown route: {self.path}"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                self._send(200, handler(kernel, payload))
            except ValueError as exc:
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
