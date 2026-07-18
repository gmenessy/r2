"""vLLM-Client: Payload-Aufbau, Parsing, Retries — über injizierten Transport."""

from __future__ import annotations

import urllib.error

import pytest

from apps.agent_layer.llm import LLMError, VLLMClient


def _completion(content: str | None = "Hallo", tool_calls: list | None = None,
                usage: dict | None = None) -> dict:
    message: dict = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {"choices": [{"message": message}],
            "usage": usage or {"prompt_tokens": 11, "completion_tokens": 7}}


class RecordingTransport:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url: str, payload: dict, headers: dict, timeout: float) -> dict:
        self.calls.append((url, payload, headers))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _client(transport, **kwargs) -> VLLMClient:
    defaults = dict(base_url="http://vllm:8000/v1", model="gemini4-31b", retries=1)
    defaults.update(kwargs)
    return VLLMClient(transport=transport, **defaults)


def test_chat_builds_openai_payload_and_parses_usage() -> None:
    transport = RecordingTransport([_completion()])
    result = _client(transport).chat(
        [{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "calc"}}],
    )
    url, payload, headers = transport.calls[0]
    assert url == "http://vllm:8000/v1/chat/completions"
    assert payload["model"] == "gemini4-31b"
    assert payload["tool_choice"] == "auto"
    assert headers["Content-Type"] == "application/json"
    assert result.content == "Hallo" and not result.wants_tools
    assert (result.prompt_tokens, result.completion_tokens) == (11, 7)
    assert result.latency_ms >= 0


def test_tool_call_arguments_are_parsed_and_bad_json_survives() -> None:
    transport = RecordingTransport([_completion(content=None, tool_calls=[
        {"id": "call_1", "function": {"name": "calc", "arguments": '{"expression": "1+1"}'}},
        {"id": "call_2", "function": {"name": "calc", "arguments": "{broken"}},
    ])])
    result = _client(transport).chat([{"role": "user", "content": "x"}])
    assert result.wants_tools and result.content == ""
    assert result.tool_calls[0].arguments == {"expression": "1+1"}
    assert "__parse_error__" in result.tool_calls[1].arguments


def test_api_key_sets_bearer_header() -> None:
    transport = RecordingTransport([_completion()])
    _client(transport, api_key="sk-x").chat([{"role": "user", "content": "hi"}])
    assert transport.calls[0][2]["Authorization"] == "Bearer sk-x"


def test_transient_error_is_retried(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    transport = RecordingTransport([urllib.error.URLError("down"), _completion()])
    result = _client(transport).chat([{"role": "user", "content": "hi"}])
    assert result.content == "Hallo" and len(transport.calls) == 2


def test_persistent_error_raises_llm_error(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    transport = RecordingTransport([urllib.error.URLError("down")] * 2)
    with pytest.raises(LLMError, match="unreachable after 2 attempts"):
        _client(transport).chat([{"role": "user", "content": "hi"}])


def test_http_4xx_is_not_retried(monkeypatch) -> None:
    """F6: Deterministische Client-Fehler sofort melden statt sinnlos wiederholen."""
    monkeypatch.setattr("time.sleep", lambda _s: None)
    error = urllib.error.HTTPError("u", 400, "Bad Request", hdrs=None, fp=None)
    transport = RecordingTransport([error, _completion()])
    with pytest.raises(LLMError, match="rejected request: 400"):
        _client(transport).chat([{"role": "user", "content": "hi"}])
    assert len(transport.calls) == 1  # kein zweiter Versuch


def test_http_5xx_is_retried(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda _s: None)
    error = urllib.error.HTTPError("u", 503, "Unavailable", hdrs=None, fp=None)
    transport = RecordingTransport([error, _completion()])
    result = _client(transport).chat([{"role": "user", "content": "hi"}])
    assert result.content == "Hallo" and len(transport.calls) == 2


def test_malformed_response_raises_llm_error() -> None:
    transport = RecordingTransport([{"choices": []}])
    with pytest.raises(LLMError, match="malformed"):
        _client(transport).chat([{"role": "user", "content": "hi"}])


def test_environment_defaults(monkeypatch) -> None:
    monkeypatch.setenv("VLLM_BASE_URL", "http://gpu-box:9000/v1/")
    monkeypatch.setenv("AGENT_MODEL", "gemini4-31b-instruct")
    client = VLLMClient(transport=RecordingTransport([]))
    assert client.base_url == "http://gpu-box:9000/v1"
    assert client.model == "gemini4-31b-instruct"
