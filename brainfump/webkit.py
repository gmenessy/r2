"""Minimaler Web-Baukasten über der Stdlib ``http.server``.

Bündelt den Boilerplate, der zuvor in jedem App-Server dupliziert war:
deklaratives Routing, JSON-/Static-Antworten, einheitliches Mapping von
Fehlern auf HTTP-Statuscodes und ein stummes Logging. Bewusst ohne externe
Abhängigkeiten — der Kernel bleibt rein Stdlib.

Beispiel::

    app = WebApp(static_dir="…")
    app.static("/", "index.html")

    @app.post("/api/echo")
    def echo(request: Request) -> dict:
        body = request.json()
        require(body, "text")
        return {"echo": body["text"]}

    server = serve(app, port=8000)
    server.serve_forever()

Handler erhalten ein :class:`Request` und geben entweder ein ``dict``
(automatisch als JSON 200 verpackt) oder eine :class:`Response` zurück.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

__all__ = [
    "HttpError",
    "Request",
    "Response",
    "WebApp",
    "json_response",
    "require",
    "serve",
    "text_response",
]


class HttpError(Exception):
    """Vom Handler geworfener Fehler mit explizitem Statuscode."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


def require(payload: dict[str, Any], *keys: str) -> None:
    """Stellt sicher, dass alle ``keys`` vorhanden und nicht ``None`` sind.

    Prüft Präsenz statt Wahrheitswert, damit gültige Nullwerte (z. B. ein
    Score von ``0.0``) nicht fälschlich als fehlend gelten.
    """
    missing = [k for k in keys if payload.get(k) is None]
    if missing:
        raise HttpError(400, f"missing fields: {missing}")


class Request:
    """Eingehende Anfrage: Methode, Pfad, Query-Parameter und Rohbody."""

    def __init__(self, method: str, path: str, query: dict[str, str], raw_body: bytes) -> None:
        self.method = method
        self.path = path
        self.query = query
        self._raw = raw_body

    def json(self) -> dict[str, Any]:
        try:
            data = json.loads(self._raw or b"{}")
        except json.JSONDecodeError as exc:
            raise HttpError(400, f"invalid JSON: {exc}")
        if not isinstance(data, dict):
            raise HttpError(400, "request body must be a JSON object")
        return data


class Response:
    """Ausgehende Antwort mit fertig kodiertem Body."""

    def __init__(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.body = body
        self.content_type = content_type
        self.status = status


def json_response(data: Any, status: int = 200) -> Response:
    return Response(json.dumps(data).encode(), "application/json", status)


def text_response(
    text: str | bytes, content_type: str = "text/plain; charset=utf-8", status: int = 200
) -> Response:
    body = text.encode() if isinstance(text, str) else text
    return Response(body, content_type, status)


Handler = Callable[[Request], "Response | dict[str, Any]"]

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
}


class WebApp:
    """Sammlung von Routen mit zentralem Dispatch und Fehler-Mapping."""

    def __init__(self, static_dir: str | None = None) -> None:
        self._routes: dict[tuple[str, str], Handler] = {}
        self._static_dir = static_dir

    def get(self, path: str) -> Callable[[Handler], Handler]:
        return self._register("GET", path)

    def post(self, path: str) -> Callable[[Handler], Handler]:
        return self._register("POST", path)

    def route(self, method: str, path: str, handler: Handler) -> None:
        self._routes[(method, path)] = handler

    def _register(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def decorator(fn: Handler) -> Handler:
            self._routes[(method, path)] = fn
            return fn

        return decorator

    def static(self, path: str, filename: str, content_type: str | None = None) -> "WebApp":
        """Registriert eine GET-Route, die eine Datei aus ``static_dir`` liefert."""
        if self._static_dir is None:
            raise ValueError("static() requires WebApp(static_dir=…)")
        resolved_type = content_type or _CONTENT_TYPES.get(
            os.path.splitext(filename)[1], "application/octet-stream"
        )
        directory = self._static_dir

        def handler(_: Request) -> Response:
            with open(os.path.join(directory, filename), "rb") as fh:
                return Response(fh.read(), resolved_type)

        self._routes[("GET", path)] = handler
        return self

    def dispatch(self, request: Request) -> Response:
        handler = self._routes.get((request.method, request.path))
        if handler is None:
            return json_response({"error": f"unknown route: {request.path}"}, 404)
        try:
            result = handler(request)
        except HttpError as exc:
            return json_response({"error": str(exc)}, exc.status)
        except (ValueError, KeyError) as exc:
            return json_response({"error": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - defensive
            return json_response({"error": str(exc)}, 500)
        return result if isinstance(result, Response) else json_response(result)


def serve(app: WebApp, host: str = "0.0.0.0", port: int = 8000) -> ThreadingHTTPServer:
    """Baut einen ``ThreadingHTTPServer``, der ``app`` bedient."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def _handle(self, method: str) -> None:
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            raw = b""
            if method == "POST":
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
            response = app.dispatch(Request(method, url.path, query, raw))
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, *args: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), _Handler)
