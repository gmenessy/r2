"""HTTP-Server für das Memory House (Stdlib, framework-frei).

Routen:
    GET  /                  Vanilla-JS-UI (Hausgrundriss)
    GET  /api/rooms         Räume
    GET  /api/room?room=    Status, Memory und Historie eines Raums
    POST /api/attempt       Aktion versuchen (läuft durch den Gatekeeper)
    POST /api/failures      Lektion speichern
    POST /api/fragile       Gerät als fragil markieren
    POST /api/rules         Hausregel anlegen (absolut oder kontextabhängig)
    POST /api/modes         Betriebsmodus setzen
    POST /api/season        Saisonwechsel als Evolution Patch
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
from apps.memory_house.house import MemoryHouse  # noqa: E402

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_server(house: MemoryHouse, host: str = "0.0.0.0", port: int = 8040) -> ThreadingHTTPServer:
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
            if url.path == "/api/rooms":
                self._send(200, {"rooms": house.rooms()})
            elif url.path == "/api/room":
                self._send(200, house.room_status(params.get("room", "wohnzimmer")))
            else:
                self._send(404, {"error": f"unknown route: {url.path}"})

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(self.rfile.read(length) or b"{}")
                room = payload.get("room", "wohnzimmer")
                if self.path == "/api/attempt":
                    self._send(
                        200,
                        house.attempt(
                            room,
                            payload["action_type"],
                            device=payload.get("device"),
                            signature=payload.get("signature"),
                            context=payload.get("context"),
                        ),
                    )
                elif self.path == "/api/failures":
                    self._send(
                        200,
                        house.remember_failure(
                            room,
                            payload["content"],
                            payload["signature"],
                            alternative=payload.get("alternative", ""),
                        ),
                    )
                elif self.path == "/api/fragile":
                    self._send(200, house.mark_fragile(room, payload["device"], payload.get("reason", "")))
                elif self.path == "/api/rules":
                    self._send(
                        200,
                        house.add_house_rule(
                            payload["text"],
                            payload["forbidden_action"],
                            when=payload.get("when"),
                        ),
                    )
                elif self.path == "/api/modes":
                    self._send(200, house.set_mode(room, payload["statement"]))
                elif self.path == "/api/season":
                    self._send(
                        200,
                        house.change_season(
                            room, payload["card_id"], payload["new_statement"], payload["valid_from"]
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

    parser = argparse.ArgumentParser(description="Memory House")
    parser.add_argument("--data", default=os.environ.get("BRAINFUMP_DATA", "./data"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8040")))
    args = parser.parse_args()

    house = MemoryHouse(BrainFumpKernel(args.data))
    house.seed_demo()
    server = create_server(house, port=args.port)
    print(f"Memory House auf http://0.0.0.0:{args.port} (Daten: {args.data})")
    server.serve_forever()


if __name__ == "__main__":  # pragma: no cover
    main()
