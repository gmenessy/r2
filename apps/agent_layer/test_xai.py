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


def test_prune_by_age_removes_old_runs_and_steps() -> None:
    """S3-5/O6: Runs vor dem Stichtag verschwinden samt Schritten."""
    import time as _time

    traces = TraceStore()
    _seed_run(traces, "run_old")
    # Ein altes Startdatum erzwingen.
    traces._conn.execute("UPDATE runs SET started = ? WHERE run_id = 'run_old'",
                         (_time.time() - 40 * 86400,))
    traces._conn.commit()
    _seed_run(traces, "run_fresh")

    removed = traces.prune(older_than_days=30)
    assert removed == 1
    assert traces.trace("run_old") is None
    assert traces.trace("run_fresh") is not None
    # Schritte des alten Runs sind ebenfalls weg.
    leftover = traces._conn.execute(
        "SELECT COUNT(*) FROM steps WHERE run_id = 'run_old'").fetchone()[0]
    assert leftover == 0


def test_prune_keeps_last_n_per_tenant() -> None:
    traces = TraceStore()
    for i in range(5):
        traces.begin(f"run_{i}", "acme", f"ziel {i}")
        traces.finish(f"run_{i}", "ok", "x")
    traces.begin("run_other", "rival", "z")
    traces.finish("run_other", "ok", "x")

    removed = traces.prune(keep_last_n_per_tenant=2)
    assert removed == 3  # acme: 5→2, rival: 1→1 (unberührt)
    acme_left = [r["run_id"] for r in traces.recent(limit=10) if r["tenant"] == "acme"]
    assert len(acme_left) == 2
    assert traces.trace("run_other") is not None


def test_prune_without_criteria_is_noop() -> None:
    traces = TraceStore()
    _seed_run(traces)
    assert traces.prune() == 0
    assert traces.trace("run_abc") is not None


def test_explain_reports_budget_stop() -> None:
    traces = TraceStore()
    traces.begin("run_b", "tiny", "teuer")
    traces.step("run_b", "budget_stop", {"reason": "budget exhausted"})
    traces.finish("run_b", "budget_exceeded", "aborted")
    narrative = " ".join(traces.explain("run_b")["narrative"])
    assert "Abbruch durch Billing" in narrative
