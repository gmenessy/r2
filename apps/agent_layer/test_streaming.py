"""trace_sse_events: die extrahierte SSE-Generatorlogik isoliert getestet."""

from __future__ import annotations

from apps.agent_layer.streaming import trace_sse_events
from apps.agent_layer.xai import TraceStore


def test_streams_existing_steps_and_terminates_on_done() -> None:
    traces = TraceStore()
    traces.begin("run_1", "acme", "Ziel")
    traces.step("run_1", "llm_call", {"prompt_tokens": 5})
    traces.finish("run_1", "ok", "42")

    events = list(trace_sse_events(traces, "run_1"))
    assert any(b"event: step" in e for e in events)
    assert events[-1].startswith(b"event: done")
    assert b'"status": "ok"' in events[-1]


def test_unknown_run_emits_stream_error_by_default() -> None:
    traces = TraceStore()
    events = list(trace_sse_events(traces, "run_missing"))
    assert len(events) == 1
    # Bewusst "stream_error" statt "error": ein SSE-Feld namens "error" würde
    # im Browser mit EventSource.onerror kollidieren (Verbindungsfehler).
    assert events[0].startswith(b"event: stream_error")
    assert b"unknown run" in events[0]


def test_wait_for_begin_polls_until_trace_exists(monkeypatch) -> None:
    traces = TraceStore()
    calls = {"n": 0}

    def fake_sleep(_s: float) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            traces.begin("run_late", "acme", "Ziel")
            traces.finish("run_late", "ok", "fertig")

    monkeypatch.setattr("apps.agent_layer.streaming.time.sleep", fake_sleep)
    events = list(trace_sse_events(traces, "run_late", wait_for_begin=True, poll_s=0.001))
    assert events[0].startswith(b": waiting")
    assert events[-1].startswith(b"event: done")
