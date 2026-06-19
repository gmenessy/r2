"""HTTP-Server für das Memory House (auf brainfump.webkit).

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

import os
import sys
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from brainfump import BrainFumpKernel  # noqa: E402
from brainfump.webkit import Request, WebApp, require, serve  # noqa: E402
from apps.memory_house.house import MemoryHouse  # noqa: E402

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def build_app(house: MemoryHouse) -> WebApp:
    app = WebApp(static_dir=_STATIC_DIR)
    app.static("/", "index.html").static("/index.html", "index.html").static("/app.js", "app.js")

    @app.get("/api/rooms")
    def _rooms(request: Request) -> dict:
        return {"rooms": house.rooms()}

    @app.get("/api/room")
    def _room(request: Request) -> dict:
        return house.room_status(request.query.get("room", "wohnzimmer"))

    @app.post("/api/attempt")
    def _attempt(request: Request) -> dict:
        body = request.json()
        require(body, "action_type")
        return house.attempt(
            body.get("room", "wohnzimmer"),
            body["action_type"],
            device=body.get("device"),
            signature=body.get("signature"),
            context=body.get("context"),
        )

    @app.post("/api/failures")
    def _failures(request: Request) -> dict:
        body = request.json()
        require(body, "content", "signature")
        return house.remember_failure(
            body.get("room", "wohnzimmer"),
            body["content"],
            body["signature"],
            alternative=body.get("alternative", ""),
        )

    @app.post("/api/fragile")
    def _fragile(request: Request) -> dict:
        body = request.json()
        require(body, "device")
        return house.mark_fragile(body.get("room", "wohnzimmer"), body["device"], body.get("reason", ""))

    @app.post("/api/rules")
    def _rules(request: Request) -> dict:
        body = request.json()
        require(body, "text", "forbidden_action")
        return house.add_house_rule(body["text"], body["forbidden_action"], when=body.get("when"))

    @app.post("/api/modes")
    def _modes(request: Request) -> dict:
        body = request.json()
        require(body, "statement")
        return house.set_mode(body.get("room", "wohnzimmer"), body["statement"])

    @app.post("/api/season")
    def _season(request: Request) -> dict:
        body = request.json()
        require(body, "card_id", "new_statement", "valid_from")
        return house.change_season(
            body.get("room", "wohnzimmer"), body["card_id"], body["new_statement"], body["valid_from"]
        )

    return app


def create_server(house: MemoryHouse, host: str = "0.0.0.0", port: int = 8040) -> ThreadingHTTPServer:
    return serve(build_app(house), host=host, port=port)


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
