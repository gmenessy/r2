"""Tests für den gemeinsamen Web-Baukasten (Ticket M-1)."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from brainfump.webkit import (
    HttpError,
    Request,
    Response,
    WebApp,
    json_response,
    require,
    serve,
    text_response,
)


def make_request(method="POST", path="/x", query=None, body=None):
    raw = json.dumps(body).encode() if body is not None else b""
    return Request(method, path, query or {}, raw)


def test_require_allows_falsy_but_present_values():
    require({"score": 0.0, "flag": False}, "score", "flag")  # darf nicht werfen
    with pytest.raises(HttpError) as exc:
        require({"a": 1}, "a", "b", "c")
    assert exc.value.status == 400
    assert "b" in str(exc.value) and "c" in str(exc.value)


def test_require_treats_none_as_missing():
    with pytest.raises(HttpError):
        require({"a": None}, "a")


def test_request_json_parses_object():
    assert make_request(body={"x": 1}).json() == {"x": 1}


def test_request_json_rejects_non_object():
    with pytest.raises(HttpError) as exc:
        Request("POST", "/x", {}, b"[1, 2, 3]").json()
    assert exc.value.status == 400


def test_request_json_rejects_invalid_json():
    with pytest.raises(HttpError) as exc:
        Request("POST", "/x", {}, b"{not json").json()
    assert exc.value.status == 400


def test_dispatch_dict_handler_becomes_json_200():
    app = WebApp()
    app.route("GET", "/ping", lambda request: {"pong": True})
    response = app.dispatch(make_request("GET", "/ping"))
    assert response.status == 200
    assert json.loads(response.body) == {"pong": True}


def test_dispatch_unknown_route_404():
    response = WebApp().dispatch(make_request("GET", "/nope"))
    assert response.status == 404
    assert "unknown route" in json.loads(response.body)["error"]


def test_dispatch_maps_value_error_to_400():
    app = WebApp()

    @app.post("/boom")
    def _boom(request: Request) -> dict:
        raise ValueError("kaputt")

    response = app.dispatch(make_request("POST", "/boom"))
    assert response.status == 400
    assert json.loads(response.body)["error"] == "kaputt"


def test_dispatch_maps_http_error_status():
    app = WebApp()

    @app.get("/auth")
    def _auth(request: Request) -> dict:
        raise HttpError(403, "verboten")

    response = app.dispatch(make_request("GET", "/auth"))
    assert response.status == 403


def test_dispatch_unexpected_error_500():
    app = WebApp()

    @app.get("/crash")
    def _crash(request: Request) -> dict:
        raise RuntimeError("unerwartet")

    assert app.dispatch(make_request("GET", "/crash")).status == 500


def test_response_helpers():
    assert json_response({"a": 1}).content_type == "application/json"
    md = text_response("# Titel", "text/markdown; charset=utf-8")
    assert md.content_type == "text/markdown; charset=utf-8"
    assert md.body == b"# Titel"


def test_health_routes():
    app = WebApp()
    app.health("demo-service", "1.2.3")
    health = app.dispatch(make_request("GET", "/api/health"))
    assert health.status == 200
    assert json.loads(health.body) == {"status": "ok", "service": "demo-service"}
    version = app.dispatch(make_request("GET", "/api/version"))
    assert json.loads(version.body) == {"service": "demo-service", "version": "1.2.3"}


def test_static_requires_static_dir():
    with pytest.raises(ValueError):
        WebApp().static("/", "index.html")


def test_static_serves_file(tmp_path):
    (tmp_path / "index.html").write_text("<h1>Hallo</h1>")
    app = WebApp(static_dir=str(tmp_path))
    app.static("/", "index.html")
    response = app.dispatch(make_request("GET", "/"))
    assert response.status == 200
    assert response.body == b"<h1>Hallo</h1>"
    assert response.content_type == "text/html; charset=utf-8"


def test_serve_end_to_end():
    app = WebApp()

    @app.post("/api/double")
    def _double(request: Request) -> dict:
        body = request.json()
        require(body, "n")
        return {"result": body["n"] * 2}

    @app.get("/api/echo")
    def _echo(request: Request) -> Response:
        return text_response(request.query.get("msg", ""), "text/plain; charset=utf-8")

    server = serve(app, host="127.0.0.1", port=0)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/double",
            data=json.dumps({"n": 21}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request) as r:
            assert json.loads(r.read())["result"] == 42

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/echo?msg=hi") as r:
            assert r.read() == b"hi"

        # Fehlendes Pflichtfeld → 400
        bad = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/double",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(bad)
        assert exc.value.code == 400
    finally:
        server.shutdown()
