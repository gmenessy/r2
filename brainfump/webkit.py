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
import logging
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

# Standard-Obergrenze für Request-Bodies (1 MiB). Schützt vor
# Memory-Exhaustion durch übergroße POSTs; pro serve()-Aufruf überschreibbar.
DEFAULT_MAX_BODY_BYTES = 1_048_576

_access_logger = logging.getLogger("brainfump.access")


def _ensure_access_handler() -> None:
    if not _access_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s [access] %(message)s"))
        _access_logger.addHandler(handler)
        _access_logger.setLevel(logging.INFO)

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

    def health(self, service: str, version: str) -> "WebApp":
        """Registriert einheitliche ``/api/health``- und ``/api/version``-Routen.

        ``/api/health`` dient dem Docker-``HEALTHCHECK`` (kein Argument nötig);
        ``/api/version`` macht den ausgelieferten Stand abfragbar.
        """

        def _health(_: Request) -> dict[str, Any]:
            return {"status": "ok", "service": service}

        def _version(_: Request) -> dict[str, Any]:
            return {"service": service, "version": version}

        self._routes[("GET", "/api/health")] = _health
        self._routes[("GET", "/api/version")] = _version
        return self

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


def serve(
    app: WebApp,
    host: str = "0.0.0.0",
    port: int = 8000,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    access_log: bool | None = None,
    read_timeout: float | None = 30.0,
) -> ThreadingHTTPServer:
    """Baut einen ``ThreadingHTTPServer``, der ``app`` bedient.

    ``max_body_bytes`` deckelt POST-Bodies (sonst ``413``).
    ``access_log`` aktiviert strukturiertes Zugriffslogging mit Latenz;
    ``None`` (Default) liest die Umgebungsvariable ``BRAINFUMP_ACCESS_LOG``
    (alles außer ``""``/``0``/``false`` schaltet es ein).
    """
    if access_log is None:
        access_log = os.environ.get("BRAINFUMP_ACCESS_LOG", "").lower() not in ("", "0", "false")
    if access_log:
        _ensure_access_handler()

    class _Handler(BaseHTTPRequestHandler):
        # Socket-Read-Timeout gegen Slowloris (langsame/teildaten-Verbindungen,
        # die sonst einen Worker-Thread dauerhaft belegen).
        timeout = read_timeout

        def do_GET(self) -> None:  # noqa: N802 (http.server API)
            self._handle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._handle("POST")

        def _handle(self, method: str) -> None:
            start = time.perf_counter()
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            raw = b""
            if method == "POST":
                # Content-Length defensiv parsen: nicht-numerisch oder negativ
                # (würde sonst das Body-Limit umgehen und rfile.read(-1) bis EOF
                # auslösen) -> 400.
                try:
                    length = int(self.headers.get("Content-Length", 0))
                except (TypeError, ValueError):
                    length = -1
                if length < 0:
                    self._reject(method, url.path, start, 400, "invalid Content-Length")
                    return
                if length > max_body_bytes:
                    self._reject(
                        method, url.path, start, 413,
                        f"request body too large (>{max_body_bytes} bytes)",
                    )
                    return
                raw = self.rfile.read(length)
            response = app.dispatch(Request(method, url.path, query, raw))
            self._write(response)
            self._access(method, url.path, response.status, start)

        def _reject(self, method: str, path: str, start: float, status: int, msg: str) -> None:
            self._write(json_response({"error": msg}, status))
            self._access(method, path, status, start)

        def _write(self, response: Response) -> None:
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            self.wfile.write(response.body)

        def _access(self, method: str, path: str, status: int, start: float) -> None:
            if access_log:
                _access_logger.info(
                    "%s %s -> %d (%.1f ms)", method, path, status, (time.perf_counter() - start) * 1000
                )

        def log_message(self, *args: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), _Handler)
