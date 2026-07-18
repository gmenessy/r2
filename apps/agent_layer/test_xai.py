"""xAI: Trace-Vollständigkeit und Explain-Verdichtung."""

from __future__ import annotations

from apps.agent_layer.xai import TraceStore


def _seed_run(traces: TraceStore, run_id: str = "run_abc") -> str:
    traces.begin(run_id, "acme", "Rechne 6*7")
    traces.step(run_id, "memory_hits", {"hits": [
        {"statement": "Nutzer bevorzugt knappe Antworten.", "memory_type": "preference"},
    ]})
    traces.step(run_id, "llm_call", {"prompt_tokens": 120, "completion_tokens": 15,
                                     "tool_calls": ["calc"]}, duration_ms=210.0)
    traces.step(run_id, "tool_call", {
        "tool": "calc", "args": {"expression": "6*7"},
        "gate": {"mode": "allow", "allowed": True},
        "sandbox": {"exit_reason": "ok"},
        "outcome": {"ok": True, "value": {"result": 42}},
    }, duration_ms=12.5)
    traces.step(run_id, "llm_call", {"prompt_tokens": 150, "completion_tokens": 8,
                                     "tool_calls": []})
    traces.finish(run_id, "ok", "42")
    return run_id


def test_trace_preserves_order_and_payloads() -> None:
    traces = TraceStore()
    run_id = _seed_run(traces)
    trace = traces.trace(run_id)
    assert trace["status"] == "ok" and trace["answer"] == "42"
    assert [s["seq"] for s in trace["steps"]] == [1, 2, 3, 4]
    assert trace["steps"][2]["payload"]["outcome"]["value"] == {"result": 42}
    assert trace["steps"][1]["duration_ms"] == 210.0
    assert trace["finished"] >= trace["started"]


def test_unknown_run_returns_none() -> None:
    traces = TraceStore()
    assert traces.trace("run_missing") is None
    assert traces.explain("run_missing") is None


def test_explain_condenses_causal_chain() -> None:
    traces = TraceStore()
    run_id = _seed_run(traces)
    explanation = traces.explain(run_id)
    assert explanation["llm"] == {"calls": 2, "prompt_tokens": 270, "completion_tokens": 23}
    assert explanation["memories_used"][0]["memory_type"] == "preference"
    assert explanation["tool_calls"][0]["tool"] == "calc"
    narrative = " ".join(explanation["narrative"])
    assert "Memory-Treffer" in narrative
    assert "Gatekeeper=allow" in narrative and "Sandbox=ok" in narrative
    assert "2 LLM-Schritte" in narrative


def test_explain_reports_budget_stop() -> None:
    traces = TraceStore()
    traces.begin("run_b", "tiny", "teuer")
    traces.step("run_b", "budget_stop", {"reason": "budget exhausted"})
    traces.finish("run_b", "budget_exceeded", "aborted")
    narrative = " ".join(traces.explain("run_b")["narrative"])
    assert "Abbruch durch Billing" in narrative
