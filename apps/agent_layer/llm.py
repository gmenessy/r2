"""OpenAI-kompatibler Chat-Client für vLLM.

vLLM stellt ``/v1/chat/completions`` bereit; der Client spricht dieses
Protokoll mit reiner Stdlib (``urllib``). Das Ziel-Setup ist ein
gemini4-31B-Modell hinter vLLM auf einer GPU-/CPU-Instanz, während die
Plattform selbst als schlanker CPU-Container läuft.

Der HTTP-Transport ist injizierbar (``transport=``), damit Tests und
Offline-Betrieb ohne Netz auskommen — dieselbe Stelle, an der auch ein
alternativer Provider eingehängt werden kann.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

DEFAULT_BASE_URL = "http://vllm:8000/v1"
DEFAULT_MODEL = "gemini4-31b"

Transport = Callable[[str, dict[str, Any], dict[str, str], float], dict[str, Any]]


class LLMError(Exception):
    """Kommunikations- oder Protokollfehler gegenüber dem LLM-Backend."""


def estimate_tokens(text: str) -> int:
    """Grobe, backend-unabhängige Token-Schätzung (~4 Zeichen/Token).

    Dient der Billing-Reservierung (S3-3), bevor das Backend die echten
    Zahlen liefert — konservativ genug, um das Budget vorab zu binden."""
    return max(1, len(text) // 4)


def estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(str(message.get("content") or "")) for message in messages)


@dataclass(frozen=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatResult:
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


def _http_transport(
    url: str, payload: dict[str, Any], headers: dict[str, str], timeout: float
) -> dict[str, Any]:  # pragma: no cover - echter Netzwerkpfad
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


class VLLMClient:
    """Minimaler Chat-Completions-Client mit Retries und Tool-Calling.

    Konfiguration über Argumente oder Umgebung:
    ``VLLM_BASE_URL`` (Default ``http://vllm:8000/v1``),
    ``AGENT_MODEL`` (Default ``gemini4-31b``), ``VLLM_API_KEY`` (optional).
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        retries: int = 2,
        transport: Transport | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("VLLM_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.model = model or os.environ.get("AGENT_MODEL", DEFAULT_MODEL)
        self.api_key = api_key or os.environ.get("VLLM_API_KEY")
        self.timeout = timeout
        self.retries = retries
        self._transport = transport or _http_transport

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> ChatResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        url = f"{self.base_url}/chat/completions"
        start = time.perf_counter()
        data = self._post_with_retries(url, payload, headers)
        latency_ms = (time.perf_counter() - start) * 1000
        return self._parse(data, latency_ms)

    def _post_with_retries(
        self, url: str, payload: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                return self._transport(url, payload, headers, self.timeout)
            except urllib.error.HTTPError as exc:
                # 4xx ist deterministisch (kaputte Anfrage, Auth) — ein Retry
                # mit identischem Payload kann nie gelingen (Finding F6).
                if exc.code < 500:
                    raise LLMError(f"LLM backend rejected request: {exc.code} {exc.reason}")
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2.0 ** attempt * 0.5, 4.0))
            except (urllib.error.URLError, OSError, TimeoutError) as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(2.0 ** attempt * 0.5, 4.0))
        raise LLMError(f"LLM backend unreachable after {self.retries + 1} attempts: {last_error}")

    @staticmethod
    def _parse(data: dict[str, Any], latency_ms: float) -> ChatResult:
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"malformed completion response: {exc}")

        tool_calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            raw_args = function.get("arguments", "{}")
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (json.JSONDecodeError, TypeError):
                # Kaputte Argument-Payload nicht verwerfen: das Tool-Ergebnis
                # meldet den Parsefehler zurück, das Modell kann korrigieren.
                arguments = {"__parse_error__": str(raw_args)[:500]}
            tool_calls.append(
                ToolCall(
                    call_id=call.get("id", f"call_{len(tool_calls)}"),
                    name=function.get("name", ""),
                    arguments=arguments,
                )
            )

        usage = data.get("usage") or {}
        return ChatResult(
            content=message.get("content") or "",
            tool_calls=tool_calls,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=latency_ms,
        )
